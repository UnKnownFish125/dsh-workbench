# dsh-workbench · worktree-server (P0)

Standalone backend service implementing the `dsh-workbench` worktree API from
[`docs/api-contract.md`](../docs/api-contract.md) §3. Pure stdlib
(`sqlite3` + `http.server`); no third-party deps, no coupling to deepmemory /
dsh-longlongchat.

## Files

| File | Purpose |
|---|---|
| `worktree.py` | SQLite store: schema (§1), node serialization (§2), all store operations. |
| `server.py` | `ThreadingHTTPServer` that routes the §3 endpoints and enforces auth (§0). |
| `tests/test_worktree.py` | `unittest` smoke tests (store-level + HTTP-level). |

## Run

```bash
/opt/AstrBot/venv/bin/python3 server.py
```

Listens on `http://localhost:6271` (configurable via env).

## Env vars

| Var | Default | Meaning |
|---|---|---|
| `WORKBENCH_SERVER_PORT` | `6271` | Listen port (prod `6270`, test `6271`). |
| `WORKBENCH_DATA_DIR` | `./data` | Where `worktree.db` and the default token file live. |
| `WORKBENCH_API_TOKEN_FILE` | `<data_dir>/api-token` | Bearer token file. |
| `WORKBENCH_CONTRACT_BASE` | parent of `worktree-server/` | Base dir used to resolve `contract_ref` (`docs/xxx.md`). |

## Auth

- Any request carrying an `Origin` header → **403**.
- Any `OPTIONS` request → **403**.
- `Authorization: Bearer <token>` is required; a missing/mismatched token → **401**
  (memory-server same-stack pattern).
- The token file is auto-provisioned at `0600` via `secrets` if absent, so bearer
  auth is always enforced. A pre-provisioned token is never overwritten.

## Endpoints

Base: `/v1/worktree`

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/health` | Health. |
| POST | `/v1/worktree/nodes` | Create node (409 if path exists). |
| GET | `/v1/worktree/nodes?workspace=` | List nodes (optional workspace filter). |
| GET | `/v1/worktree/nodes/<path>` | Get node + its links. |
| POST | `/v1/worktree/nodes/<path>` | Patch node. |
| DELETE | `/v1/worktree/nodes/<path>` | Delete node (and its links). |
| GET | `/v1/worktree/tree?root=&depth=&workspace=` | Tree (forest if `root` omitted). |
| GET | `/v1/worktree/ancestors/<path>` | Ancestor chain root→node (incl. node). |
| GET | `/v1/worktree/contract/<path>` | Contract payload (+ file text if readable). |
| POST | `/v1/worktree/session-link` | Mount a session on a node (idempotent upsert). |
| GET | `/v1/worktree/session/<id>` | Resolve session → node + ancestors. |
| DELETE | `/v1/worktree/session-link` | Unmount a session (`{session_id}`). |
| POST | `/v1/worktree/import` | Bulk import (`{source, root_path, workspace?, nodes?}`). |

`<path>` may contain `/` and is matched as a whole path; URL-encoded segments
(`deepmemory%2Fmemory-server`) are decoded before matching.

Errors return `{"error": "<message>"}` with status codes per §5
(`400` / `403` / `404` / `409` / `500`).

## Test

```bash
cd worktree-server
/opt/AstrBot/venv/bin/python3 -m py_compile worktree.py server.py
/opt/AstrBot/venv/bin/python3 tests/test_worktree.py
```
