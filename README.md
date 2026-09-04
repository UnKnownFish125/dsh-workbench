# dsh-workbench — the non-linear workbench for DeepSeek Harness

[![Platform: DeepSeek Harness](https://img.shields.io/badge/Platform-DeepSeek%20Harness-4c8dff)](https://github.com/deepseek-ai)

`dsh-workbench` gives DeepSeek Harness agents a **complete project structure
tree** and acts as a **unified session manager**. A worktree node holds a
component / service / interface / contract; a session can be mounted on a node,
and child nodes **inherit the upper layer's context** automatically. Instead of
an agent re-deriving a project from scattered code and docs, it can read the
structure programmatically and open a session that already knows the ancestor
chain.

View the full design rationale in [`docs/DESIGN.md`](docs/DESIGN.md) and the
exact HTTP/JSON contract in [`docs/api-contract.md`](docs/api-contract.md).

## Why

| Pain | This project |
|---|---|
| Another agent doesn't know the project structure | **Structure tree** — component / service / interface / contract as structured nodes |
| Sessions start cold with no background | **Inherited injection** — a node-mounted session carries its ancestor summary automatically |
| Scattered sessions are hard to manage | **Session manager** — mount sessions on nodes, view them on one card |
| Contracts live in docs/code the AI must hunt for | Node `contract_ref` → contract doc, readable via three channels (inject / tool / doc) |

## Three-layer carrier

1. **Structure tree (worktree)** — structured storage (`data/worktree.db`), readable
   programmatically by the AI.
2. **Contract / interface detail** — documents (`contract_ref` → `docs/xxx.md`),
   read verbatim for review / citation.
3. **State / memory** — provided by deepmemory (this repo is compatible, not
   coupled: it does not read or write deepmemory's DB; it only records the
   association via `node_links(kind='memory')` for display).

## Modules

| Module | Location | Responsibility | Owner |
|---|---|---|---|
| P0 service | `worktree-server/` | Python REST API + SQLite store (port `6271` test / `6270` prod) | P0 |
| P1 preset plugin | `agent-preset/_worktree-plugin/` | Inherited injection (`[WORKTREE]`, order 46) + tools | P1 |
| P2 web plugin | `web-plugin/` | DSH client plugin: tree + session cards, `/worktree-api` proxy | P2 |
| P3 import/seed | `tools/` | `import-agents.py`, `seed-demo.py`, `verify.sh` | P3 |
| Docs | `docs/`, `CONTEXT.md` | contract + design + install + contexts | shared |

## Quick start

```bash
# 1. run the server on the test port (6271) — stdlib only
WORKBENCH_SERVER_PORT=6271 /opt/AstrBot/venv/bin/python3 worktree-server/server.py
curl -s http://127.0.0.1:6271/v1/health

# 2. seed the deepmemory acceptance sample (idempotent)
WORKBENCH_SERVER_PORT=6271 /opt/AstrBot/venv/bin/python3 tools/seed-demo.py

# 3. import your own project tree
WORKBENCH_SERVER_PORT=6271 /opt/AstrBot/venv/bin/python3 \
  tools/import-agents.py --root-path /path/to/project --dry-run
WORKBENCH_SERVER_PORT=6271 /opt/AstrBot/venv/bin/python3 \
  tools/import-agents.py --root-path /path/to/project

# 4. repo-level gate
bash tools/verify.sh
```

Automated install to a DSH host is documented in
[`docs/INSTALL.md`](docs/INSTALL.md).

## API summary

Base: `/v1/worktree` (see [`docs/api-contract.md`](docs/api-contract.md) for
shapes, auth, and error semantics).

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/health` | Liveness. |
| POST | `/v1/worktree/nodes` | Create node (`409` if path exists). |
| GET | `/v1/worktree/nodes?workspace=` | List (optional workspace filter). |
| GET | `/v1/worktree/nodes/<path>` | Node + links. |
| POST | `/v1/worktree/nodes/<path>` | Patch node (used on 409). |
| DELETE | `/v1/worktree/nodes/<path>` | Delete node + links. |
| GET | `/v1/worktree/tree?root=&depth=&workspace=` | Tree (forest if `root` omitted). |
| GET | `/v1/worktree/ancestors/<path>` | Ancestor chain root→node (includes node). |
| GET | `/v1/worktree/contract/<path>` | Node + contract ref (+ file text when readable). |
| POST | `/v1/worktree/session-link` | Mount session on a node (idempotent). |
| GET | `/v1/worktree/session/<id>` | Resolve session → node + ancestors. |
| DELETE | `/v1/worktree/session-link` | Unmount session. |
| POST | `/v1/worktree/import` | Bulk import (`{source, root_path, workspace?, nodes?}`). |

`<path>` may contain `/` and is matched whole; URL-encoded segments
(`deepmemory%2Fmemory-server`) are decoded before matching. Node primary key =
`path`; `parent_path` forms the tree edge. Errors return `{"error": "<msg>"}`
with `400 / 403 / 404 / 409 / 500`.

## Boundary — independence & compatibility

- **Compatible with deepmemory** (coexists): it never reads or writes deepmemory's
  DB, recording associations only via `node_links(kind='memory')`. Separate repo,
  separate DB, separate namespace `dsh-workbench`.
- **Independent of `dsh-longlongchat`**: a completely different repo — no shared
  namespace, no runtime dependency, no references.
- The worktree does **not** replace DSH session logs — session logs remain the
  single source of truth.
- It does **not** do memory / knowledge retrieval — that is deepmemory's job.
- The two-phase deployment rule means production port **6270** is never touched
  during development (test `6271` only).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`CHANGELOG.md`](CHANGELOG.md).
Run `bash tools/verify.sh` before any merge; it gates python syntax, JS syntax,
ESM load, and the P0 test suite.

## License

**AGPL-3.0** (see [`LICENSE`](LICENSE)). This is an independent native
implementation for DeepSeek Harness, not a code port.
