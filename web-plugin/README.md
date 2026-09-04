# dsh-workbench · web-plugin (P2)

DSH Web client plugin for the **工作台 (Workbench)** — a non-linear project
workbench. It renders a **structure tree** (from the worktree-server) on the
left and a **session manager** on the right, letting you mount DSH sessions
onto tree nodes so child nodes inherit the upper-layer context.

## Layout

| File | Role |
|---|---|
| `index.js` | Host half. Registers the `settingsNamespace('dsh-workbench')` discovery contract and a same-origin proxy `/worktree-api/*` forwarding to `localhost:6271` (test) / `6270` (prod; env `WORKBENCH_SERVER_PORT`). Injects the `WORKBENCH_API_TOKEN_FILE` Bearer token. |
| `client.js` | Browser half. Registers a `conversation.view` tab `id: workbench`, label `工作台`. |
| `dsh.patch.yml` | Bundle patch entry `id: workbench-ui`, `name: dsh-workbench`. |
| `package.json` | Module manifest (`dsh.category: Workbench`, `dsh.client.inject` runtime list, `dsh.bundle.patch`). |

## Host proxy

Mirrors the reference deepmemory host plugin exactly: `kind:'prefix'` route
`/worktree-api` that strips `Origin`/`Authorization`, re-injects the service
Bearer token, and pipes the body to the upstream. A convenience exact route for
`POST /worktree-api/v1/worktree/session-link` demonstrates the host-initiated
JSON request (`workbenchRequest`) plus clean error mapping.

## Client session API (confirmed vs assumed)

The DSH client runtime is injected via `dsh.client.inject` and also surfaced to
`conversation.view` entries as standard props. What I could confirm by
inspecting the installed runtime type definitions
(`@deepseek-ai/dsh-client-runtime`):

- **Confirmed** — inside `apply(ctx)`:
  - `ctx.get('sessions')` → `SessionRuntime`, whose public surface is
    `open(id)`, `create(opts)`, `fork(opts)`, `refresh()`, `clear()`, and the
    `list` store (`list.current`, build from `useSessions`).
  - `ctx.get('workspaces')` → `WorkspaceRuntime` (`list`, `startSession`, …).
- **Confirmed** — as `conversation.view` entry props:
  - `props.useSessions((s) => …)` → `SessionListState` (`ids`, `byId`,
    `current`, `phase`); each row carries `id`, `displayTitle`, `title`, `cwd`,
    `running`, `blank`, `agentPreset`, `parentId`.
  - `props.useWorkspaces((s) => …)` → `WorkspaceListState.items` (`workspaceId`,
    `sessionIds`, …).
  - `props.sessionId` → the current session id.

**Assumed** (documented here since no in-repo caller was found):
- `sessionService.create(opts)` returns a `Promise<SessionId>` and that calling
  `sessionService.open(id)` both selects the session as current and addresses
  its scope. This is the "New session" flow: `create` (targeting the current
  session's workspace, else its cwd) then `open`.
- `POST /v1/worktree/session-link` idempotence (upsert) is taken from the API
  contract; the client treats a 2xx as success.

**Deliberately omitted (DESIGN D-11):** any "给指令 / send-message" button. The
injected runtime exposes no confirmed message-send API, so this plugin only
offers **open / switch / create / mount-to-node**. A "send-message" button was
never added.

## Verification

- `node --check` passes for `index.js` (ESM) and `client.js` (plain JS inside
  the `__ModuleLoader__` wrapper).
- Portfolio: `index.js` + `client.js` + `dsh.patch.yml` + `package.json`.
- Proxy host port default = `6271`; prefix = `/worktree-api`.

## Notes

- Independent of deepmemory / longlongchat (own namespace `dsh-workbench`, zero
  runtime coupling). Compatible with deepmemory; the worktree only records
  `node_links(kind='memory')` relations for display, never reads/writes its DB.
- Deployment is test-env-first (see repo `DESIGN.md`).
