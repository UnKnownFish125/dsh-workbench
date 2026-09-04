# dsh-workbench 设计文档（ADR）

> 对齐 `workbench-plan.md` v0.3（GLM 审核后修订）。本文件记录关键决策与理由。
> 独立于 `dsh-longlongchat`；与 deepmemory 并存兼容，但不读/写其数据。

## 决策记录

| 编号 | 决策 | 理由 |
|---|---|---|
| D-01 | 独立仓库 `dsh-workbench`（服务+插件+前端+文档同仓） | 独立发布/市场；不耦合 deepmemory/longlongchat |
| D-02 | 后端独立 Python 服务 `dsh-workbench.service`（端口 6270 生产 / 6271 测试） | 首版独立（R-M1）；62xx 段分配 6270/6271 |
| D-03 | 存储 `data/worktree.db`（SQLite，独立于记忆/知识） | 结构=机器级事实，与记忆解耦（R-A/R-B） |
| D-04 | 节点主键 `id INTEGER AUTOINCREMENT` + `path TEXT UNIQUE`；`parent_path` 为树边 | R-B 代理键；path 为外部规范键 |
| D-05 | 全局单树（结构为机器级事实，跨工作区恒可见） | 与 `scope='global'` 语义一致（R-B#3） |
| D-06 | 鉴权同 literature：api-token(Bearer) + 拒绝浏览器 Origin + 同源 `/worktree-api` 代理 | 保持一致安全模型 |
| D-07 | 继承注入 = `systemPrompt.context({name:'worktree', order:46, text})`；TTL 300s 冻结 | 与已部署 order 表对齐（约束前提=45，工作台=46，记忆=50） |
| D-08 | 注入格式 `[WORKTREE] <root > … > node` + 每层一行摘要；≤500 token；≥5 层截断 | 预算可控（R-M2） |
| D-09 | 会话挂节点：`node_links(kind='session')` + `GET /v1/worktree/session/<id>` 解析 | P1 注入取数 + P2 会话卡共用 |
| D-10 | P2 自绘 React 树+会话卡（不并入 synapse 画布） | 解耦、清晰；synapse 仅可选并存（条件验收 #6） |
| D-11 | "给指令" API 未证实 → P2 保留开/停/切换/新建/挂载，去掉"给指令"（到 P2 spike 证实后再开） | R-C 已证伪，避免过期接口 |
| D-12 | `node_links UNIQUE(node_id,kind,ref)` | 防重复挂载（R-B#2） |

## 分层

```
┌─ ① 结构树（worktree 表）── 结构化存储（API 程序化读"结构"）
├─ ② 契约/接口详情 ── 文档（contract_ref → docs/xxx.md；AI 三通道读取）
└─ ③ 状态/记忆 ── deepmemory（已有；节点可挂记忆 doc-link，仅展示）
```

## AI 读取三通道
1. **注入**：祖先前缀（自动，P1，order:46）
2. **工具**：`worktree_descendants(path)` / `worktree_contract(path)` / `worktree_ancestors(path)`（按需深挖，P1 工具）
3. **文档**：`contract_ref` 原文（评审/引用）

## 与 longlongchat 边界
- 独立仓库、独立服务、独立插件命名空间（`dsh-workbench`）、无运行时依赖。
- 不依赖 longlongchat 补丁；longlongchat 补丁在 `dsh-web.service`（生产）叠于 DSH 之上，workbench 以独立 service + preset 插件 + client 插件加入，二者互不引用。

## 部署两阶段铁律
- 生产变更先测试机(3091/6271) 全验 → 生产(3081/6270)。本工作台开发阶段**仅在测试机部署**，不触碰生产。

## 验收对齐（plan §9）
1. 结构树：注册 deepmemory 三层示例（root/主体/服务，含端口+契约 ref），树 API 遍历正确。
2. 继承注入：memory-server 节点开会话 → `[WORKTREE]` 段出现全链摘要；回合内冻结。
3. 多会话：树节点挂 2+ 会话，卡片开/停/切换，会话独立运行。
4. AI 读：`worktree_contract(memory-server)` 拿到契约。
5. 预算：注入增量 ≤500 token。
6. synapse 兼容（条件验收，仅启用时）。
