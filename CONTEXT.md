# Context

`dsh-workbench` 是 DeepSeek Harness 的非线性工作台（基于 `workbench-plan.md` v0.3）。

## 一句话
工程项目需要**完整的项目结构树**告诉其它 agent 项目的结构/接口/契约；工作台 = **统一会话管理器**（树节点挂会话，子节点继承上层上下文）。

## 三层载体
1. **结构树（worktree）**：结构化存储（`data/worktree.db`），AI 程序化读"结构"。
2. **契约/接口详情**：文档（`contract_ref` → `docs/xxx.md`），逐字读/评审。
3. **状态/记忆**：deepmemory（已有），节点可挂记忆（仅展示）。

## 关键常量
- 端口：生产 `6270` / 测试 `6271`。
- 注入 order：`46`（工作台 `[WORKTREE]`，介于轨 B 约束前提=45 与记忆=50 之间）。
- 注入预算：≤500 token；祖先链 ≥5 层截断；TTL 300s 回合内冻结。

## 边界
- 不替代 DSH 会话日志（唯一事实源）。
- 不做记忆/知识检索（deepmemory/literature 的职责）。
- **独立于 `dsh-longlongchat`**（不同仓库、不同命名空间 `dsh-workbench`、零依赖）。
- **与 deepmemory 兼容并存**：不改 deepmemory db；仅 `node_links(kind='memory')` 记录关联（展示用）。

## 模块
| 模块 | 位置 | 说明 |
|---|---|---|
| P0 服务 | `worktree-server/` | Python 服务，worktree API（6270/6271） |
| P1 插件 | `agent-preset/_worktree-plugin/` | 继承注入 + 工具（order:46） |
| P2 前端 | `web-plugin/` | DSH client 插件（树+会话卡） |
| P3 导入 | `tools/import-agents.py` | AGENTS.md / frontmatter 自动导入 |
