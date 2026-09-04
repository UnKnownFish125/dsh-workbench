# dsh-workbench API 契约（v0.1）

> 本文件是 **P0 服务 / P1 插件 / P2 前端 / P3 导入** 的统一接口约定。
> 每个模块以此契约编码，模块之间 **只经由本契约耦合**，任一模块不得假设其它模块的实现细节。

## 0. 总体约定

- 传输：JSON（`Content-Type: application/json`），UTF-8。
- 基址：`/v1/worktree`。
- 鉴权与 memory-server 同栈（见 R-A）：
  1. 拒绝浏览器 Origin 跨源请求（`Origin` 头存在 → `403`）；`OPTIONS` 一律 `403`。
  2. 服务读取 `WORKBENCH_API_TOKEN_FILE`（默认 `data/api-token`）中的 token，要求 `Authorization: Bearer <token>`；无 token 文件则放行（仅回环）。
- 端口：生产 `6270`，测试 `6271`（env `WORKBENCH_SERVER_PORT`）。db：`data/worktree.db`。
- 节点外部主键：**`path`**（`'deepmemory/memory-server'`），唯一。`id` 为代理键（R-B）。
- 时间：`created_at`/`updated_at` 为 Unix 秒（`REAL`）。

## 1. 数据模型（SQLite `data/worktree.db`）

```sql
CREATE TABLE IF NOT EXISTS worktree_nodes (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  path         TEXT UNIQUE NOT NULL,                 -- 'deepmemory/memory-server'
  parent_path  TEXT,                                  -- 父节点 path，根=NULL
  kind         TEXT NOT NULL CHECK(kind IN ('root','component','service','interface','contract','doc')),
  name         TEXT NOT NULL,
  desc         TEXT,
  contract_ref TEXT,                                  -- 契约文档路径（docs/xxx.md）
  meta         TEXT,                                  -- JSON：{port,path,interfaces[],deps[],status,workspace,...}
  created_at   REAL NOT NULL,
  updated_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON worktree_nodes(parent_path);

CREATE TABLE IF NOT EXISTS node_links (
  node_id    INTEGER NOT NULL,                        -- -> worktree_nodes.id
  kind       TEXT NOT NULL CHECK(kind IN ('contract','session','memory')),
  ref        TEXT NOT NULL,                           -- session: session_id；doc: path；memory: memory id
  created_at REAL NOT NULL,
  UNIQUE(node_id, kind, ref)
);
CREATE INDEX IF NOT EXISTS idx_links_node      ON node_links(node_id);
CREATE INDEX IF NOT EXISTS idx_links_kind_ref  ON node_links(kind, ref);
```

## 2. 节点序列化（统一形状）

每个返回的 `node` 对象：

```json
{
  "id": 1,
  "path": "deepmemory/memory-server",
  "parent_path": "deepmemory/主体",
  "kind": "service",
  "name": "memory-server",
  "desc": "语义长期记忆后端服务",
  "contract_ref": "docs/deepmemory-literature-export-contract.md",
  "meta": { "port": 6230, "path": "data/memory.db", "interfaces": ["export-archive","get_sources"], "status": "active", "workspace": "deepseek-harness" },
  "workspace": "deepseek-harness",
  "created_at": 1765000000.0,
  "updated_at": 1765000000.0
}
```

- `meta` 为 **已解析对象**（非字符串）。`workspace` 顶层字段 = `meta.workspace` 的冗余，用于展示/筛选。
- 约定 `meta` 键：`port`（端口）、`path`（数据/配置路径）、`interfaces`（数组）、`deps`（数组）、`status`、`workspace`，可自由扩展。

## 3. 端点

### 3.1 健康
```
GET /v1/health
  -> 200 {"ok":true,"version":"0.1.0","db":"worktree.db"}
```

### 3.2 节点 CRUD
```
POST /v1/worktree/nodes
  body: {path, parent_path?, kind, name, desc?, contract_ref?, meta?, workspace?}
  -> 200 {"node":{...nodeshape...}}
  -> 409 {"error":"node path exists"}   若 path 已存在（创建与更新分离；更新走 POST /<path>）

GET /v1/worktree/nodes?workspace=
  -> 200 {"nodes":[{...nodeshape...}]}

GET /v1/worktree/nodes/<path>
  -> 200 {"node":{...}, "links":[{node_id,kind,ref,created_at}...]}

POST /v1/worktree/nodes/<path>
  body: {parent_path?, name?, kind?, desc?, contract_ref?, meta?, workspace?}
  -> 200 {"node":{...nodeshape...}}

DELETE /v1/worktree/nodes/<path>
  -> 200 {"ok":true,"deleted":1,"links":n}
```

> 注：`<path>` 在 URL 中按 `encodeURIComponent` 分段编码（`deepmemory/memory-server` → `deepmemory%2Fmemory-server`），服务端解码后**按整段 path 匹配**（唯一索引）。

### 3.3 树
```
GET /v1/worktree/tree?root=<path>&depth=<n>&workspace=
  -> 200 {"root":{...nodeshape...}|null, "children":[{...nodeshape..., "children":[...]}...]}
  root 缺省 → 返回全部根节点（parent_path 为空）的森林：{"root":null,"children":[...]}
```

### 3.4 祖先链（继承注入取数核心）
```
GET /v1/worktree/ancestors/<path>
  -> 200 {"path":"...", "ancestors":[{...nodeshape...}...]}   // root→node 顺序，含 node 自身
```

### 3.5 契约
```
GET /v1/worktree/contract/<path>
  -> 200 {"node":{...}, "contract_ref":"...", "contract_text":null, "contract_path":null, "links":[...]}
  // contract_text：若 contract_ref 指向服务器可见的本地文件且可读，则尽量读入文本；不可读则 null。节点无 contract_ref 时 contract_ref=null。
```

### 3.6 会话挂载（P1 注入 + P2 会话卡）
```
POST /v1/worktree/session-link
  body: {node_path, session_id}
  -> 200 {"ok":true,"node_path":"...","session_id":"..."}     // upsert（幂等）

GET /v1/worktree/session/<session_id>
  -> 200 {"node_path":null|"...", "node":null|{...nodeshape...}, "ancestors":[...]}
  // 未挂载时 node_path=null,node=null,ancestors=[]

DELETE /v1/worktree/session-link
  body: {session_id}
  -> 200 {"ok":true}
```

### 3.7 导入（P3）
```
POST /v1/worktree/import
  body: {source:"agents"|"frontmatter", root_path:"...", workspace?}
  -> 200 {"imported":n, "nodes":[{...nodeshape...}...]}
```

## 4. P1 插件注入格式（供插件实现，非服务侧）

插件从 `GET /v1/worktree/session/<session_id>` 取 `ancestors`，渲染为 `systemPrompt.context({name:'worktree', order:46, text})`：

```
[WORKTREE] deepmemory > 主体 > memory-server
  · memory-server: 6230 / data/memory.db / export-archive+get_sources
  · 契约: docs/deepmemory-literature-export-contract.md
  · 子节点(可挂会话): memory-server > 后端服务 …
```

- 预算 ≤500 token；祖先链 ≥5 层截断（保留 root 与近端）。
- TTL 300s，回合内冻结（同 L2/L3 纪律）。

## 5. 错误语义

- `400` 参数/类型错；`404` 节点或会话未找到；`409` path 冲突；`403` Origin/权限；`500` 内部错。
- 错误体：`{"error":"human readable message"}`。

## 6. 非目标（边界）

- 不替代 DSH 会话（会话日志仍是唯一事实源）。
- 不做记忆/知识检索（deepmemory/literature 的职责）。
- 独立于 dsh-longlongchat（完全不同仓库，无依赖、无共享命名空间、无运行期耦合）。
- deepmemory 兼容：可并存；worktree 不读/不写 deepmemory 的 db，仅通过 node_links(kind='memory') 记录关联（展示用）。
