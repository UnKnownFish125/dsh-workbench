# Contributing

感谢关注 dsh-workbench。参与开发前请阅读以下约定。

## 验证

- 每次改动合并前必须跑 `bash tools/verify.sh`（python 语法 / node --check /
  ESM 冒烟 / P0 测试套件，PASS·SKIP·FAIL 逐项输出）。
- 任何一项 **FAIL** 都禁止进入测试机部署；`SKIP` 仅表示该步的输入工件
  （P0/P1/P2 各自拥有的文件）尚未落位，需等其他模块合并后复跑。
- 验证在临时目录、随机端口、模拟环境里执行，不触碰生产 `data`、端口与
  dsh 进程（缺失工件以 SKIP 处理，绝不伪造通过）。

## 部署两阶段铁律

- **任何生产变更（插件/server/配置）顺序必须是：测试机(3091) → 生产(3081)。**
- 本仓库开发阶段**仅在测试机（端口 6271）部署**，生产端口 **6270** 不触碰。
- 禁止：跳过测试机直接改生产；直接修改 `/dsh*` 生产目录。

## 模块归属（互不越界）

| 模块 | 位置 | 归属 |
|---|---|---|
| P0 服务 | `worktree-server/` | P0 |
| P1 预设插件 | `agent-preset/_worktree-plugin/plugin-v1.js` | P1 |
| P2 前端 | `web-plugin/` | P2 |
| P3 导入/种子 | `tools/` | P3 |

- **不得修改**其它模块拥有/正在交付的文件（如 `_worktree-plugin/plugin-v1.js`、
  `web-plugin/` 代码、`worktree-server/` 的 server/service/test 代码）。可交叉引用，
  但不改内容。
- 新增或修改请保持既有 API（`docs/api-contract.md`）、表结构与配置键兼容。

## 分支与发布

- 功能在独立分支进行，合入 main 前先通过 `bash tools/verify.sh`。
- 发布使用语义化 tag（如 `v0.1.0`）；小修复打 `.x` 尾标签，大版本打主版本号。
- 不向 main 直接提交未验证的 WIP；不 commit/push 未确认的改动。

## 范围

- `worktree-server/`：结构树数据层与 REST API。
- `agent-preset/`：workbench 预设插件（继承注入 + 工具）。
- `web-plugin/`：DSH client 树 + 会话卡视图。
- `tools/`：`import-agents.py`（AGENTS.md/目录树导入）、`seed-demo.py`（验收种子）、
  `verify.sh`（仓库门禁）。
