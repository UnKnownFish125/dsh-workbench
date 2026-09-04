__ModuleLoader__.load({
  id: 'dsh-workbench',
  factory: (require) => {
/**
 * dsh-workbench browser plugin — the "工作台" (workbench) conversation view.
 *
 * Left: the structure tree from the worktree-server (GET /worktree-api/v1/worktree/tree).
 * Right: session cards driven by the DSH client session runtime (list / open /
 * switch / create), plus "mount session to node" (POST …/v1/worktree/session-link).
 *
 * NOTE on the session API (D-11): the DSH client session runtime exposes
 * `SessionRuntime` (get via `ctx.get('sessions')`), whose confirmed surface is
 * `open(id)`, `create(opts)`, `refresh()`, and the `list` store, published to
 * conversation.view entries as `props.useSessions` / `props.useWorkspaces` /
 * `props.sessionId`. There is NO confirmed "send-message / 给指令" client API in
 * the injected runtime, so — per DESIGN D-11 — this plugin deliberately exposes
 * open / switch / create / mount-session only and never a "给指令" button. The
 * session-list/open/create calls below are documented as ASSUMPTIONS where the
 * injected runtime surface was confirmed by inspection (see README.md).
 */
const React = require('react')
const name = 'dsh-workbench'

const TYPE_ZH = { root: '根', component: '组件', service: '服务', interface: '接口', contract: '契约', doc: '文档' }
const TYPE_EN = { root: 'root', component: 'component', service: 'service', interface: 'interface', contract: 'contract', doc: 'doc' }

const I18N = {
  zh: {
    title: '工作台', lang: 'EN', refresh: '刷新', loading: '加载中…',
    tree: '结构树', rootPlaceholder: '根路径（可选）', loadRoot: '加载', treeEmpty: '（暂无节点）',
    nodeDetail: '节点详情', name: '名称', kind: '类型', path: '路径', status: '状态',
    contractRef: '契约', noContract: '（无契约）', ancestors: '祖先链', sessionCards: '会话',
    open: '打开/切换', mount: '挂载到节点', newSession: '新建会话', mounting: '挂载中…',
    mounted: '已挂载', mountFail: '挂载失败：', openFail: '打开失败：', createFail: '新建失败：',
    selectNode: '请先在左侧选择节点', noSession: '（无会话）', noSessionRuntime: '会话运行库不可用',
    desc: '说明', meta: '元信息', empty: '（无）', error: '加载失败：', detailLoadFail: '详情加载失败：',
    treeLoadFail: '树加载失败：', selected: '已选', sessionId: '会话语', workspace: '工作区',
    running: '运行中', idle: '空闲', blank: '空会话', agentPreset: '预设', expand: '展开', collapse: '收起',
  },
  en: {
    title: 'Workbench', lang: '中文', refresh: 'Refresh', loading: 'Loading…',
    tree: 'Structure Tree', rootPlaceholder: 'Root path (optional)', loadRoot: 'Load', treeEmpty: '(no nodes)',
    nodeDetail: 'Node Detail', name: 'Name', kind: 'Kind', path: 'Path', status: 'Status',
    contractRef: 'Contract', noContract: '(no contract)', ancestors: 'Ancestors', sessionCards: 'Sessions',
    open: 'Open/Switch', mount: 'Mount to node', newSession: 'New session', mounting: 'Mounting…',
    mounted: 'Mounted', mountFail: 'Mount failed: ', openFail: 'Open failed: ', createFail: 'Create failed: ',
    selectNode: 'Select a node on the left first', noSession: '(no sessions)', noSessionRuntime: 'Session runtime unavailable',
    desc: 'Description', meta: 'Meta', empty: '(empty)', error: 'Load failed: ', detailLoadFail: 'Detail load failed: ',
    treeLoadFail: 'Tree load failed: ', selected: 'Selected', sessionId: 'Session', workspace: 'Workspace',
    running: 'running', idle: 'idle', blank: 'blank', agentPreset: 'preset', expand: 'expand', collapse: 'collapse',
  },
}

const PANEL_CSS = `
.dsh-wb-panel { display:flex; flex-direction:column; height:100%; min-height:0; font-size:13px; }
.dsh-wb-toolbar { display:flex; align-items:center; gap:8px; padding:8px 14px; border-bottom:1px solid var(--dsw-alias-border-l1, rgba(128,128,128,.2)); flex-wrap:wrap; }
.dsh-wb-title { margin:0; font-size:13px; font-weight:600; opacity:.85; }
.dsh-wb-btn { border:1px solid var(--dsw-alias-border-l2, rgba(128,128,128,.45)); background:var(--dsw-alias-bg-layer-1, transparent); color:var(--dsw-alias-label-primary, inherit); border-radius:6px; padding:4px 11px; cursor:pointer; font:inherit; white-space:nowrap; }
.dsh-wb-btn:hover { background:var(--dsw-alias-bg-layer-2, rgba(128,128,128,.16)); }
.dsh-wb-btn:disabled { opacity:.5; cursor:default; }
.dsh-wb-btn-primary { border-color:var(--dsw-alias-brand-primary, #4c8dff); color:var(--dsw-alias-brand-primary, #4c8dff); }
.dsh-wb-input { flex:1; min-width:120px; background:var(--dsw-alias-bg-layer-1, transparent); border:1px solid var(--dsw-alias-border-l2, rgba(128,128,128,.4)); border-radius:6px; padding:5px 8px; color:var(--dsw-alias-label-primary, inherit); font:inherit; }
.dsh-wb-body { display:flex; flex:1; min-height:0; }
.dsh-wb-left { width:42%; min-width:260px; max-width:460px; border-right:1px solid var(--dsw-alias-border-l1, rgba(128,128,128,.2)); overflow:auto; padding:8px 6px; }
.dsh-wb-right { flex:1; min-width:0; overflow:auto; padding:10px 14px; }
.dsh-wb-msg { opacity:.75; padding:4px 14px; font-size:12px; }
.dsh-wb-tree-row { display:flex; align-items:center; gap:5px; padding:3px 6px; border-radius:5px; cursor:pointer; }
.dsh-wb-tree-row:hover { background:var(--dsw-alias-bg-layer-1, rgba(128,128,128,.1)); }
.dsh-wb-tree-row-selected { background:var(--dsw-alias-bg-layer-2, rgba(128,128,128,.2)); outline:1px solid var(--dsw-alias-brand-primary, rgba(76,141,255,.5)); }
.dsh-wb-tree-toggle { width:14px; text-align:center; opacity:.7; cursor:pointer; flex:none; }
.dsh-wb-tree-toggle-leaf { opacity:.35; }
.dsh-wb-tree-name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.dsh-wb-tree-meta { font-size:11px; opacity:.75; white-space:nowrap; }
.dsh-wb-tree-status { font-size:11px; opacity:.7; white-space:nowrap; }
.dsh-wb-kind { font-size:10px; padding:0 5px; border-radius:8px; white-space:nowrap; background:var(--dsw-alias-bg-layer-1, rgba(128,128,128,.2)); }
.dsh-wb-kind-root { background:rgba(76,141,255,.22); color:#9cc0ff; }
.dsh-wb-kind-component { background:rgba(80,180,120,.2); color:#9fdcb8; }
.dsh-wb-kind-service { background:rgba(210,140,60,.22); color:#f0c091; }
.dsh-wb-kind-interface { background:rgba(150,110,220,.22); color:#cdb6f5; }
.dsh-wb-kind-contract { background:rgba(230,120,120,.22); color:#f5b3b3; }
.dsh-wb-kind-doc { background:rgba(120,150,200,.2); color:#b9cbe8; }
.dsh-wb-contract-badge { font-size:10px; padding:0 5px; border-radius:8px; background:rgba(230,120,120,.2); color:#f0a9a9; white-space:nowrap; max-width:150px; overflow:hidden; text-overflow:ellipsis; }
.dsh-wb-section-head { display:flex; align-items:center; gap:8px; margin:12px 0 6px; }
.dsh-wb-section-title { font-size:12px; font-weight:600; opacity:.8; letter-spacing:.03em; }
.dsh-wb-box { border:1px solid var(--dsw-alias-border-l1, rgba(128,128,128,.3)); border-radius:8px; padding:10px; }
.dsh-wb-row { display:flex; gap:8px; align-items:flex-start; padding:8px 0; border-top:1px solid var(--dsw-alias-border-l1, rgba(128,128,128,.14)); }
.dsh-wb-row:first-child { border-top:none; }
.dsh-wb-session-main { flex:1; min-width:0; }
.dsh-wb-session-title { line-height:1.4; word-break:break-word; }
.dsh-wb-session-meta { display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin-top:4px; }
.dsh-wb-session-badge { font-size:11px; padding:1px 6px; border-radius:10px; background:var(--dsw-alias-bg-layer-1, rgba(128,128,128,.2)); white-space:nowrap; }
.dsh-wb-session-badge-running { background:rgba(80,180,120,.2); color:#9fdcb8; }
.dsh-wb-value { opacity:.9; line-height:1.5; word-break:break-word; }
.dsh-wb-hint { opacity:.6; font-size:12px; line-height:1.5; }
.dsh-wb-empty { opacity:.6; font-size:12px; padding:8px 0; }
@media (max-width:760px) { .dsh-wb-left { width:100%; max-width:none; border-right:none; border-bottom:1px solid var(--dsw-alias-border-l1, rgba(128,128,128,.2)); } .dsh-wb-body { flex-direction:column; } }
`

async function api(method, path, body) {
  try {
    const opts = { method, headers: { 'Content-Type': 'application/json' } }
    if (body !== undefined && body !== null) opts.body = JSON.stringify(body)
    const res = await fetch('/worktree-api' + path, opts)
    if (!res.ok) return { error: 'HTTP ' + res.status }
    return await res.json()
  } catch (e) {
    return { error: String((e && e.message) || e) }
  }
}

function apply(ctx) {
  const slots = ctx.get('slots')
  if (slots === undefined) return
  const sessionService = ctx.get('sessions')
  const workspaceService = ctx.get('workspaces')

  const styleEl = document.createElement('style')
  styleEl.dataset.plugin = 'dsh-workbench'
  styleEl.textContent = PANEL_CSS
  document.head.appendChild(styleEl)

  function kindLabel(kind, lang) {
    return (lang === 'en' ? TYPE_EN : TYPE_ZH)[kind] || kind
  }

  // ── Recursive tree item ─────────────────────────────────────────────────
  function TreeItem(props) {
    const node = props.node
    const selectedPath = props.selectedPath
    const expanded = props.expanded
    const onToggle = props.onToggle
    const onSelect = props.onSelect
    const t = props.t
    const lang = props.lang
    const meta = node.meta || {}
    const children = node.children || []
    const hasChildren = children.length > 0
    const isExpanded = !!expanded[node.path]
    const isSelected = selectedPath === node.path
    return React.createElement('div', { className: 'dsh-wb-tree-item' },
      React.createElement('div', {
        className: 'dsh-wb-tree-row' + (isSelected ? ' dsh-wb-tree-row-selected' : ''),
        style: { paddingLeft: (props.depth * 14) + 6 },
        onClick: function () { onSelect(node) },
      },
        React.createElement('span', {
          className: 'dsh-wb-tree-toggle' + (hasChildren ? '' : ' dsh-wb-tree-toggle-leaf'),
          onClick: function (e) { if (hasChildren) { e.stopPropagation(); onToggle(node.path) } },
        }, hasChildren ? (isExpanded ? '▾' : '▸') : '·'),
        React.createElement('span', { className: 'dsh-wb-kind dsh-wb-kind-' + (node.kind || 'doc') }, kindLabel(node.kind, lang)),
        React.createElement('span', { className: 'dsh-wb-tree-name' }, node.name),
        meta.port ? React.createElement('span', { className: 'dsh-wb-tree-meta' }, String(meta.port)) : null,
        meta.status ? React.createElement('span', { className: 'dsh-wb-tree-status' }, String(meta.status)) : null,
      ),
      isExpanded && hasChildren
        ? React.createElement('div', { className: 'dsh-wb-tree-children' },
            children.map(function (child) {
              return React.createElement(TreeItem, {
                key: child.path, node: child, depth: props.depth + 1,
                selectedPath: selectedPath, expanded: expanded,
                onToggle: onToggle, onSelect: onSelect, t: t, lang: lang,
              })
            }))
        : null,
    )
  }

  function WorkbenchPanel(props) {
    const [lang, setLang] = React.useState('zh')
    const [root, setRoot] = React.useState('')
    const [inputRoot, setInputRoot] = React.useState('')
    const [treeChildren, setTreeChildren] = React.useState([])
    const [treeRoot, setTreeRoot] = React.useState(null)
    const [expanded, setExpanded] = React.useState({})
    const [selectedPath, setSelectedPath] = React.useState(null)
    const [selected, setSelected] = React.useState(null) // { node, ancestors, links }
    const [busy, setBusy] = React.useState(false)
    const [busyDetail, setBusyDetail] = React.useState(false)
    const [busyLink, setBusyLink] = React.useState(false)
    const [msg, setMsg] = React.useState('')

    const sid = props && props.sessionId ? String(props.sessionId) : ''
    const sessionsState = typeof props.useSessions === 'function'
      ? props.useSessions(function (state) { return state })
      : { ids: [], byId: {}, phase: 'pending' }
    const workspaces = typeof props.useWorkspaces === 'function'
      ? props.useWorkspaces(function (state) { return (state && state.items) || [] })
      : []
    const dict = I18N[lang] || I18N.zh
    function t(key) { return dict[key] !== undefined ? dict[key] : key }
    const currentCwd = sid ? (sessionsState.byId && sessionsState.byId[sid] && sessionsState.byId[sid].cwd) : undefined
    const currentWs = workspaces.find(function (w) { return (w.sessionIds || []).indexOf(sid) >= 0 })

    async function loadTree(nextRoot) {
      setBusy(true)
      setMsg('')
      const res = await api('GET', '/v1/worktree/tree' + (nextRoot ? '?root=' + encodeURIComponent(nextRoot) : ''))
      setBusy(false)
      if (res && Array.isArray(res.children)) {
        setTreeChildren(res.children)
        setTreeRoot(res.root || null)
      } else if (res && res.error) {
        setMsg(t('treeLoadFail') + res.error)
      } else {
        setMsg(t('treeLoadFail') + 'bad response')
      }
    }

    React.useEffect(function () { loadTree(root) }, []) // eslint-disable-line

    async function selectNode(node) {
      if (!node || !node.path) return
      setSelectedPath(node.path)
      setBusyDetail(true)
      setSelected(null)
      const pathEnc = encodeURIComponent(node.path)
      const detail = await Promise.all([
        api('GET', '/v1/worktree/nodes/' + pathEnc),
        api('GET', '/v1/worktree/ancestors/' + pathEnc),
      ])
      setBusyDetail(false)
      const nodeRes = detail[0] || {}
      const ancRes = detail[1] || {}
      if (nodeRes.error) { setMsg(t('detailLoadFail') + nodeRes.error); return }
      setSelected({
        node: nodeRes.node || node,
        links: nodeRes.links || [],
        ancestors: (ancRes && ancRes.ancestors) || [],
      })
    }

    async function toggleExpand(path) {
      setExpanded(function (prev) {
        const next = Object.assign({}, prev)
        next[path] = !next[path]
        return next
      })
    }

    async function openSession(id) {
      if (!sessionService || typeof sessionService.open !== 'function') { setMsg(t('noSessionRuntime')); return }
      try { sessionService.open(id) } catch (e) { setMsg(t('openFail') + String((e && e.message) || e)) }
    }

    async function mountSession(id) {
      if (!selected || !selected.node || !selected.node.path) { setMsg(t('selectNode')); return }
      setBusyLink(true)
      setMsg('')
      const res = await api('POST', '/v1/worktree/session-link', { node_path: selected.node.path, session_id: id })
      setBusyLink(false)
      if (res && res.error) setMsg(t('mountFail') + res.error)
      else { setMsg(t('mounted') + ' · ' + id); selectNode(selected.node) }
    }

    async function createSession() {
      if (!sessionService || typeof sessionService.create !== 'function') { setMsg(t('noSessionRuntime')); return }
      setMsg('')
      try {
        const opts = {}
        if (currentWs && currentWs.workspaceId) opts.workspaceId = currentWs.workspaceId
        else if (currentCwd) opts.cwd = currentCwd
        const id = await sessionService.create(opts)
        sessionService.open(id)
      } catch (e) {
        setMsg(t('createFail') + String((e && e.message) || e))
      }
    }

    function renderTree() {
      if (busy && !treeChildren.length) return React.createElement('div', { className: 'dsh-wb-empty' }, t('loading'))
      if (!treeChildren.length) return React.createElement('div', { className: 'dsh-wb-empty' }, t('treeEmpty'))
      return treeChildren.map(function (c) {
        return React.createElement(TreeItem, {
          key: c.path, node: c, depth: 0,
          selectedPath: selectedPath, expanded: expanded,
          onToggle: toggleExpand, onSelect: selectNode, t: t, lang: lang,
        })
      })
    }

    function renderSessions() {
      const ids = sessionsState && sessionsState.ids ? sessionsState.ids : []
      if (!ids.length) {
        return React.createElement('div', { className: 'dsh-wb-empty', style: { padding: '2px 0' } },
          sessionsState && sessionsState.phase === 'pending' ? t('loading') : t('noSession'))
      }
      return ids.map(function (id) {
        const s = sessionsState.byId[id]
        if (!s) return null
        const running = !!s.running
        const badgeClass = running ? ' dsh-wb-session-badge-running' : ''
        const hasNode = !!(selected && selected.node && selected.node.path)
        return React.createElement('div', { key: String(id), className: 'dsh-wb-row' },
          React.createElement('div', { className: 'dsh-wb-session-main' },
            React.createElement('div', { className: 'dsh-wb-session-title' }, s.displayTitle || s.title || String(id)),
            React.createElement('div', { className: 'dsh-wb-session-meta' },
              React.createElement('span', { className: 'dsh-wb-session-badge' + badgeClass }, running ? t('running') : t('idle')),
              s.blank ? React.createElement('span', { className: 'dsh-wb-session-badge' }, t('blank')) : null,
              s.agentPreset ? React.createElement('span', { className: 'dsh-wb-session-badge' }, String(s.agentPreset)) : null,
            ),
          ),
          React.createElement('div', { style: { display: 'flex', gap: 6, flexShrink: 0 } },
            React.createElement('button', {
              className: 'dsh-wb-btn dsh-wb-btn-primary',
              onClick: function () { openSession(id) },
              title: String(id),
            }, t('open')),
            React.createElement('button', {
              className: 'dsh-wb-btn',
              disabled: !hasNode || busyLink,
              onClick: function () { mountSession(id) },
              title: hasNode ? '' : t('selectNode'),
            }, busyLink ? t('mounting') : t('mount')),
          ),
        )
      })
    }

    function renderDetail() {
      if (!selectedPath && !selected) return React.createElement('div', { className: 'dsh-wb-empty' }, t('selectNode'))
      if (busyDetail || !selected) return React.createElement('div', { className: 'dsh-wb-empty' }, t('loading'))
      const node = selected.node
      const meta = node.meta || {}
      return React.createElement('div', { className: 'dsh-wb-box' },
        React.createElement('div', { className: 'dsh-wb-section-head' },
          React.createElement('span', { className: 'dsh-wb-section-title' }, t('nodeDetail') + ' · ' + (node.name || '')),
        ),
        React.createElement('div', { className: 'dsh-wb-row' },
          React.createElement('span', { style: { width: 72, opacity: .65 } }, t('name') + ':'),
          React.createElement('span', { className: 'dsh-wb-value' }, node.name),
        ),
        React.createElement('div', { className: 'dsh-wb-row' },
          React.createElement('span', { style: { width: 72, opacity: .65 } }, t('kind') + ':'),
          React.createElement('span', { className: 'dsh-wb-value' }, kindLabel(node.kind, lang) + ' (' + node.kind + ')'),
        ),
        React.createElement('div', { className: 'dsh-wb-row' },
          React.createElement('span', { style: { width: 72, opacity: .65 } }, t('path') + ':'),
          React.createElement('span', { className: 'dsh-wb-value' }, node.path),
        ),
        node.desc ? React.createElement('div', { className: 'dsh-wb-row' },
          React.createElement('span', { style: { width: 72, opacity: .65 } }, t('desc') + ':'),
          React.createElement('span', { className: 'dsh-wb-value' }, node.desc),
        ) : null,
        React.createElement('div', { className: 'dsh-wb-row' },
          React.createElement('span', { style: { width: 72, opacity: .65 } }, t('contractRef') + ':'),
          node.contract_ref
            ? React.createElement('span', { className: 'dsh-wb-contract-badge', title: node.contract_ref }, node.contract_ref)
            : React.createElement('span', { className: 'dsh-wb-hint' }, t('noContract')),
        ),
        meta.port ? React.createElement('div', { className: 'dsh-wb-row' },
          React.createElement('span', { style: { width: 72, opacity: .65 } }, t('meta') + ':'),
          React.createElement('span', { className: 'dsh-wb-value' }, String(meta.port) + (meta.status ? ' · ' + String(meta.status) : '')),
        ) : null,
        selected.ancestors && selected.ancestors.length
          ? React.createElement('div', { className: 'dsh-wb-box', style: { marginTop: 8 } },
              React.createElement('div', { className: 'dsh-wb-section-head' },
                React.createElement('span', { className: 'dsh-wb-section-title' }, t('ancestors')),
              ),
              selected.ancestors.map(function (a) {
                return React.createElement('div', { key: a.path, className: 'dsh-wb-value', style: { padding: '2px 0' } },
                  a.kind ? React.createElement('span', { className: 'dsh-wb-kind dsh-wb-kind-' + (a.kind || 'doc'), style: { marginRight: 6 } }, kindLabel(a.kind, lang)) : null,
                  String(a.name || a.path))
              }))
          : null,
      )
    }

    return React.createElement('div', { className: 'dsh-wb-panel' },
      React.createElement('div', { className: 'dsh-wb-toolbar' },
        React.createElement('span', { className: 'dsh-wb-title' }, t('title')),
        React.createElement('input', {
          className: 'dsh-wb-input', value: inputRoot, placeholder: t('rootPlaceholder'),
          onChange: function (e) { setInputRoot(e.target.value) },
          onKeyDown: function (e) { if (e.key === 'Enter') { setRoot(inputRoot.trim()); loadTree(inputRoot.trim()) } },
        }),
        React.createElement('button', {
          className: 'dsh-wb-btn', disabled: busy,
          onClick: function () { setRoot(inputRoot.trim()); loadTree(inputRoot.trim()) },
        }, t('loadRoot')),
        React.createElement('button', { className: 'dsh-wb-btn', disabled: busy, onClick: function () { loadTree(root) } }, busy ? t('loading') : t('refresh')),
        React.createElement('button', { className: 'dsh-wb-btn', onClick: function () { setLang(lang === 'zh' ? 'en' : 'zh') } }, t('lang')),
      ),
      msg ? React.createElement('div', { className: 'dsh-wb-msg' }, msg) : null,
      React.createElement('div', { className: 'dsh-wb-body' },
        React.createElement('div', { className: 'dsh-wb-left' },
          React.createElement('div', { className: 'dsh-wb-section-head' },
            React.createElement('span', { className: 'dsh-wb-section-title' }, t('tree')),
            treeRoot ? React.createElement('span', { className: 'dsh-wb-session-badge', title: treeRoot.path }, String(treeRoot.name || treeRoot.path)) : null,
          ),
          renderTree(),
        ),
        React.createElement('div', { className: 'dsh-wb-right' },
          renderDetail(),
          React.createElement('div', { className: 'dsh-wb-section-head', style: { marginTop: 14 } },
            React.createElement('span', { className: 'dsh-wb-section-title' }, t('sessionCards')),
            React.createElement('button', {
              className: 'dsh-wb-btn dsh-wb-btn-primary',
              onClick: createSession,
            }, t('newSession')),
          ),
          React.createElement('div', { className: 'dsh-wb-box' }, renderSessions()),
        ),
      ),
    )
  }

  slots.inject('conversation.view', function () {
    return slots.register(
      { name: 'conversation.view', id: 'workbench', order: 5, label: '工作台' },
      function (props) { return React.createElement(WorkbenchPanel, props) },
    )
  })
}

// Module export contract: the factory returns {name, apply} (mirrors the
// reference client half). `apply(ctx)` above performs the side-effectful
// registration and does NOT return the module.
return { name, apply }
  }
})
