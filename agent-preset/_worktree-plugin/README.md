# dsh-workbench — agent preset plugin (P1)

预设层插件，为 DSH agent 提供 **worktree 结构树的继承上下文** 与 **按需深挖工具**。
命名空间 `dsh-workbench`（独立于 `dsh-longlongchat`，与 deepmemory 并存不耦合）。

- 插件文件：`plugin-v1.js`
- 导出：`name = 'dsh-workbench'`，`inject = ['tools']`
- 数据源：`worktree-server`（P0，端口生产 `6270` / 测试 `6271`），经 host 的 `/worktree-api` 同源代理访问（契约基址 `/v1/worktree`）。

## 1. 继承注入（`[WORKTREE]` 段，order 46）

每个会话被挂载到一个 worktree 节点后，agent 的 system prompt 会自动获得该节点的祖先链摘要：

```
[WORKTREE] deepmemory > 主体 > memory-server
  · deepmemory
  · 主体
  · memory-server: 6230 / data/memory.db / export-archive+get_sources
  · 契约: docs/deepmemory-literature-export-contract.md
  · 子节点(可挂会话): memory-server > 后端服务, 前端
```

- 解析：会话 id 取自 **`context.agent.id`**（AGENT-scope 的会话 id 是 `agent.id`，**不是** `agent.session.id`；见 `_memory-plugin/plugin-v3.js`）。经 `systemPrompt.context({name:'worktree',order:46,text:(context)=>{…}})`（包在 `ctx.effect(...)`）注册段，并由 `ctx.on('system-prompt/assemble', …)` 按 `agent.id` 刷新缓存并覆写该段，调用
  `GET /worktree-api/v1/worktree/session/<session_id>` 取回 `node` / `ancestors`。
- 每层一行摘要：`name: port / path / interfaces.join('+')`（空部分省略）；`contract_ref` 单独一行。
- 预算：≤500 token。祖先链 >5 层时保留 root + 近端 4；子节点行最多列 8 个。
- TTL 300s，回合内冻结：缓存按会话 id（`agent.id`），仅当 `session/event` 记录的该会话 user/message 计数变化（新用户消息）且 TTL 已过时才重新拉取；回合内复用已解析文本。
- 降级：节点未挂载或任何取数失败时渲染为空串，绝不抛错。

## 2. 工具

| 工具 | 端点 | 说明 |
|---|---|---|
| `worktree_descendants(path, depth?)` | `GET /v1/worktree/tree?root=<path>&depth=<n>` | 取以 path 为根的子树；depth 缺省 1 |
| `worktree_ancestors(path)` | `GET /v1/worktree/ancestors/<path>` | 取 root→node 的祖先链（含自身） |
| `worktree_contract(path)` | `GET /v1/worktree/contract/<path>` | 取节点 + 契约 ref + （可读时）契约文本 |

三个工具均返回紧凑可读对象，供 agent 在需要时查看完整结构 / 契约原文。

## 3. 把一个会话挂到节点

会话挂载是 **P1 注入取数 + P2 前端会话卡** 共用的入口（契约 §3.6）。

- 通过前端（P2 web-plugin 会话卡）挂载，或直接调用接口：

```bash
# 挂载（幂等 upsert）：把 session_id 挂到 node_path
curl -s -X POST http://127.0.0.1:6270/v1/worktree/session-link \
  -H 'Content-Type: application/json' \
  -d '{"node_path":"deepmemory/主体/memory-server","session_id":"<session_id>"}'
# -> {"ok":true,"node_path":"deepmemory/主体/memory-server","session_id":"<session_id>"}

# 验证：当前会话挂到了哪个节点，并取其祖先链
curl -s http://127.0.0.1:6270/v1/worktree/session/<session_id>
# -> {"node_path":null|"...","node":{...}|null,"ancestors":[...]}

# 解除挂载
curl -s -X DELETE http://127.0.0.1:6270/v1/worktree/session-link \
  -H 'Content-Type: application/json' -d '{"session_id":"<session_id>"}'
```

- 测试机走 `6271`，生产走 `6270`。经 host `/worktree-api` 代理时对应
  `/worktree-api/v1/worktree/session-link`（同源，浏览器端不直连服务端口）。

> 说明：url 中 `<path>` 按段 `encodeURIComponent`（`a/b` → `a%2Fb`），服务端按整段 path 匹配。

## 4. 边界

- 不替代 DSH 会话日志（唯一事实源）。
- 不做记忆/知识检索（deepmemory / literature 的职责）。
- 不读/不写 deepmemory 的数据；仅通过 `node_links(kind='memory')` 记录关联（展示用）。
