# dsh-workbench — agent preset plugin (P1) implementation

> P1 owns `agent-preset/_worktree-plugin/plugin-v1.js`; **do not modify it here**.
> This note summarises what it delivers so P3/P2 and reviewers can cross-reference.

## What it is

A DSH **preset component** (namespace `dsh-workbench`) that gives an agent the
project's structure context automatically. It is a pure JS preset plugin — no
server, no DB.

## Key facts

| Item | Value |
|---|---|
| File | `agent-preset/_worktree-plugin/plugin-v1.js` |
| `name` | `dsh-workbench` |
| `inject` | `['tools']` |
| Data source | `worktree-server` (P0), port prod `6270` / test `6271`, via host `/worktree-api` same-origin proxy |
| Read path | `GET /v1/worktree/session/<session_id>` → `node` + `ancestors` |

## Behaviors

1. **Inherited injection (`[WORKTREE]`, order 46).** For a session mounted on a
   node, injects the ancestor-chain summary into the system prompt as
   `systemPrompt.context({ name: 'worktree', order: 46, text })`:
   ```
   [WORKTREE] deepmemory > 主体 > memory-server
     · memory-server: 6230 / data/memory.db / export-archive+get_sources
     · 契约: docs/deepmemory-literature-export-contract.md
     · 子节点(可挂会话): memory-server > 后端服务 …
   ```
   - One line per layer: `name: port / path / interfaces.join('+')` (empty parts
     omitted); `contract_ref` on its own line.
   - Budget ≤500 token; ancestor chain >5 layers truncates to root + nearest 4;
     child rows capped at 8.
   - TTL **300s**, frozen within a turn (per `WORKBENCH_SERVER_PORT`).
   - Degrade-to-empty on any lookup failure — never throws.

2. **Tools (read deeper on demand).**

   | Tool | Endpoint |
   |---|---|
   | `worktree_descendants(path, depth?)` | `GET /v1/worktree/tree?root=<path>&depth=<n>` |
   | `worktree_ancestors(path)` | `GET /v1/worktree/ancestors/<path>` |
   | `worktree_contract(path)` | `GET /v1/worktree/contract/<path>` |

3. **Session mount.** Session↔node mounting is shared with P2's session cards
   (`POST /v1/worktree/session-link`, idempotent; see contract §3.6).

## Verification

- `node --check plugin-v1.js` (JS syntax)
- `node --input-type=module -e "await import('<abs path>')"` (ESM load smoke — catches
  CJS-loose misses)
- Repo gate: `bash tools/verify.sh` runs both plus the P0 test suite.

## Boundaries

- Does not replace DSH session logs (single source of truth).
- Does not do memory / knowledge retrieval (deepmemory / literature territory).
- Never reads/writes deepmemory's DB; only `node_links(kind='memory')` for display.
