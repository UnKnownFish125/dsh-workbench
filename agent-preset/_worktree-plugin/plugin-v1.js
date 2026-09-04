// dsh-workbench agent preset plugin (P1) — 结构树继承注入 + 工具
// 职责：
//   1. 继承注入（order:46）：从会话挂载节点解析祖先链，渲染 [WORKTREE] 段（systemPrompt.context + assemble）。
//   2. 工具：worktree_descendants / worktree_ancestors / worktree_contract。
// 数据源：worktree-server（P0，6270/6271），经 host 的 /worktree-api 同源代理（契约基址 /v1/worktree）。
//
// 注入模式严格镜像已验证的 AGENT-scope 参考：
//   /www/dsh-test-home/.agent-presets/_memory-plugin/plugin-v3.js（lines 473-522）
//   - session id = context.agent.id（AGENT-scope 的会话标识，不是嵌套 session.id 字段）
//   - systemPrompt.context({ name, order, text }) 包在 ctx.effect(...)
//   - ctx.on('system-prompt/assemble', ...) 里 await next()、按 sessionId 刷新缓存、覆写 sections
//   - 每会话缓存 ~300s TTL + 回合内冻结（仅 user/message 计数变化才重新刷新）；失败降级空串。

import { defineTool } from '/usr/local/node/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-tools/lib/index.js'

export const name = 'dsh-workbench'

export const inject = ['tools']

// API 基址（相对 DSH host origin）。契约基址 /v1/worktree，经 /worktree-api 代理。
const API = '/worktree-api/v1/worktree'

// 注入预算 / TTL（契约 §4 + DESIGN D-07/D-08）
const TTL_MS = 300000          // 300s
const MAX_CHAIN = 5            // 祖先链 >=5 层截断（保留 root + 近端 4）
const MAX_CHILDREN = 8         // 子节点行最多列 8 个
const MAX_RENDER_CHARS = 2000  // 粗粒度安全上限（≈ 500 token 的保守近似）

// 与 literature-kb 一致的 fetch helper（相对 host origin，鉴权由 /worktree-api 代理承载）。
async function api(path, opts = {}) {
  const { method = 'GET', body } = opts
  const res = await fetch(API + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`)
  return data
}

// ════════════════════════════════════════════════
// 渲染 [WORKTREE] 段（契约 §4）—— 纯函数
// ════════════════════════════════════════════════
// 单行摘要：`name: port / path / interfaces.join('+')`（空部分省略）
function nodeLine(node) {
  const meta = node && node.meta ? node.meta : {}
  const parts = []
  if (meta.port != null && meta.port !== '') parts.push(String(meta.port))
  if (meta.path) parts.push(String(meta.path))
  if (Array.isArray(meta.interfaces) && meta.interfaces.length) parts.push(meta.interfaces.join('+'))
  const desc = parts.length ? ': ' + parts.join(' / ') : ''
  return '· ' + ((node && node.name) || '') + desc
}

// 祖先链 >= 5 层截断：保留 root + 近端 4（契约 §4）
function trimChain(chain) {
  if (!chain || chain.length <= MAX_CHAIN) return chain || []
  return [chain[0], ...chain.slice(-(MAX_CHAIN - 1))]
}

// 渲染整个 [WORKTREE] 块；node 缺失时返回 ''；任何错误降级为空串（不抛出）。
async function renderWorktree(r) {
  const ancestors = (r && Array.isArray(r.ancestors)) ? r.ancestors : []
  const node = r && r.node
  const chain = trimChain(ancestors.length ? ancestors : (node ? [node] : []))

  const title = chain.map((a) => a && a.name).filter(Boolean).join(' > ')
  if (!title) return ''

  const lines = []
  for (const a of chain) lines.push('  ' + nodeLine(a))

  // 契约（每层如有 contract_ref 各列一行；通常仅叶子携带）
  for (const a of chain) {
    if (a && a.contract_ref) lines.push('  · 契约: ' + a.contract_ref)
  }

  // 子节点（可挂会话）—— best-effort，失败则整行省略
  const nodePath = node && node.path
  if (nodePath) {
    try {
      const t = await api('/tree?root=' + encodeURIComponent(nodePath) + '&depth=1')
      const children = (t && Array.isArray(t.children)) ? t.children : []
      if (children.length) {
        const names = children.slice(0, MAX_CHILDREN).map((c) => c && c.name).filter(Boolean)
        const suffix = children.length > MAX_CHILDREN ? ' …' : ''
        if (names.length) {
          lines.push('  · 子节点(可挂会话): ' + ((node.name) || '') + ' > ' + names.join(', ') + suffix)
        }
      }
    } catch (e) { /* 子节点行降级：省略 */ }
  }

  let text = '[WORKTREE] ' + title + '\n' + lines.join('\n')
  // 粗粒度预算守卫：超限则仅保留标题 + 叶子节点行 + 契约 + 子节点行
  if (text.length > MAX_RENDER_CHARS) {
    const titleLine = '[WORKTREE] ' + title
    const last = chain.length ? chain[chain.length - 1] : null
    const keep = [titleLine]
    if (last) keep.push('  ' + nodeLine(last))
    for (const a of chain) if (a && a.contract_ref) keep.push('  · 契约: ' + a.contract_ref)
    if (lines.some((l) => l.includes('子节点(可挂会话)'))) {
      keep.push(lines.find((l) => l.includes('子节点(可挂会话)')))
    }
    text = keep.join('\n')
  }
  return text
}

export function apply(ctx) {
  const outSchema = { type: 'object', additionalProperties: true }
  const textRender = (value) => [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value) }]

  // ── 每会话状态：TTL 缓存 + 回合内冻结（userCounts 变化才重刷） ──
  const state = { userCounts: {} }
  const cache = new Map()                 // sessionId -> { text, at }
  const userCountBySession = new Map()    // sessionId -> 该会话上次已处理的 user/message 计数
  const initialized = new Set()           // sessionId 已解析过（用于回合内冻结判断）
  const refreshes = new Map()             // sessionId -> in-flight refresh promise（去重）
  const pendingRefresh = new Map()        // sessionId -> 本回合刷新 promise（冻结时返回）

  function sessionIdOf(session) {
    if (session && typeof session.id === 'string') return session.id
    if (session && session.header) {
      if (typeof session.header.id === 'string') return session.header.id
      if (typeof session.header.sessionId === 'string') return session.header.sessionId
    }
    return ''
  }

  // 解析某会话的 [WORKTREE] 文本：命中 TTL 缓存直接返回；否则向服务端取数。
  // 任何取数失败降级为 ''（绝不抛错），并标记 initialized（回合内冻结）。
  async function resolveWorktreeText(sessionId) {
    const key = String(sessionId)
    if (!key) return ''
    if (refreshes.has(key)) return refreshes.get(key)
    const refresh = (async () => {
      const cached = cache.get(key)
      if (cached && Date.now() - cached.at < TTL_MS) return cached.text
      let text = ''
      try {
        const r = await api('/session/' + encodeURIComponent(key))
        text = await renderWorktree(r)
      } catch (e) {
        text = ''
      }
      cache.set(key, { text, at: Date.now() })
      initialized.add(key)
      return text
    })().finally(() => refreshes.delete(key))
    refreshes.set(key, refresh)
    return refresh
  }

  // 回合内冻结：仅在 user/message 计数变化（新用户消息）时启动新刷新；否则复用本回合已在途的刷新。
  function ensureWorktreeRefresh(sessionId) {
    const key = String(sessionId)
    const cur = (state.userCounts && state.userCounts[key]) || 0
    const last = userCountBySession.get(key) || 0
    if (initialized.has(key) && cur === last) return pendingRefresh.get(key) || null
    userCountBySession.set(key, cur)
    const p = resolveWorktreeText(key).finally(() => pendingRefresh.delete(key))
    pendingRefresh.set(key, p)
    return p
  }

  // ── 继承注入（order:46）：systemPrompt.context + assemble ──
  const systemPrompt = ctx.get('systemPrompt')
  if (systemPrompt) {
    ctx.effect(() => systemPrompt.context({
      name: 'worktree',
      order: 46,
      text: (context) => {
        // 注意：AGENT-scope 的会话 id 是 agent.id（非嵌套 session.id 字段）——见 memory-plugin v3
        const agent = context && context.agent
        const sessionId = agent && agent.id ? String(agent.id) : ''
        const base = sessionId && cache.get(sessionId) ? String(cache.get(sessionId).text || '') : ''
        return base || ''
      },
    }))
  } else {
    console.error('[worktree] systemPrompt unavailable')
  }

  ctx.on('system-prompt/assemble', async (assembly, context, next) => {
    const assembled = await next()
    const agent = context && context.agent
    const sessionId = agent && agent.id ? String(agent.id) : ''
    if (!sessionId) return assembled
    try {
      // 触发/复用本回合刷新，然后读缓存（冻结时命中已解析文本）
      await ensureWorktreeRefresh(sessionId)
    } catch (e) {
      console.error('[worktree] prompt cache refresh failed', String(e))
    }
    const text = (cache.get(sessionId) && cache.get(sessionId).text) ? String(cache.get(sessionId).text) : ''
    if (!assembled || !Array.isArray(assembled.sections)) return assembled
    return {
      ...assembled,
      sections: assembled.sections.map((section) => section.name === 'worktree' ? { ...section, text: text } : section),
    }
  })

  // 用户消息到达即启动刷新（回合内冻结：仅 userCount 变化才重刷）
  ctx.on('session/event', async (session, event) => {
    try {
      const t = event && event.type
      const sid = sessionIdOf(session)
      if (!sid) return
      if (t === 'compaction/summary') {
        initialized.delete(sid)
        return
      }
      if (t !== 'user/message' && t !== 'assistant/message') return
      if (t === 'user/message') {
        state.userCounts = state.userCounts || {}
        state.userCounts[sid] = (state.userCounts[sid] || 0) + 1
        ensureWorktreeRefresh(sid)
      }
    } catch (e) { /* 事件处理失败不影响注入 */ }
  })

  // ── 工具（按需深挖）────────────
  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'worktree_descendants',
    description: 'Get the subtree under a worktree node path (root->children). depth defaults to 1.',
    parameters: {
      path: { type: 'string', required: true, description: 'Full node path, e.g. "deepmemory/memory-server".' },
      depth: { type: 'integer', description: 'Max traversal depth.', default: 1 },
    },
    output: { schema: outSchema, render: textRender },
    async execute(args) {
      const q = ['root=' + encodeURIComponent(String(args.path || ''))]
      if (args.depth != null) q.push('depth=' + encodeURIComponent(String(args.depth)))
      const data = await api('/tree?' + q.join('&'))
      return { ok: true, root: data.root || null, children: data.children || [] }
    },
  })))

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'worktree_ancestors',
    description: 'Get the ancestor chain (root->node) for a worktree node path, including the node itself.',
    parameters: {
      path: { type: 'string', required: true, description: 'Full node path, e.g. "deepmemory/memory-server".' },
    },
    output: { schema: outSchema, render: textRender },
    async execute(args) {
      const data = await api('/ancestors/' + encodeURIComponent(String(args.path || '')))
      return { ok: true, path: data.path || null, ancestors: data.ancestors || [] }
    },
  })))

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'worktree_contract',
    description: 'Get a worktree node with its contract reference and (if readable) contract text.',
    parameters: {
      path: { type: 'string', required: true, description: 'Full node path, e.g. "deepmemory/memory-server".' },
    },
    output: { schema: outSchema, render: textRender },
    async execute(args) {
      const data = await api('/contract/' + encodeURIComponent(String(args.path || '')))
      return {
        ok: true,
        node: data.node || null,
        contract_ref: data.contract_ref || null,
        contract_text: data.contract_text || null,
        contract_path: data.contract_path || null,
        links: data.links || [],
      }
    },
  })))
}
