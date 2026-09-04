# Install — dsh-workbench (test machine)

> **Two-phase iron rule.** All development deploys go to the **test machine only**
> (workbench port **6271**). The production stack (**6270**) is **never touched**
> during development. Verify fully on 3091 first; production promotion is a
> separate, deliberate step.

This guides installing `dsh-workbench` on the test machine (host **3091**) from a
clone of this repo. It installs the three shipped components plus the seed demo:

| Component | Repo module | Role | Runtime |
|---|---|---|---|
| P0 service | `worktree-server/` | Worktree REST API + SQLite store | stdlib Python, port `6271` |
| P1 agent preset | `agent-preset/_worktree-plugin/` | Inherited injection (`[WORKTREE]`, order 46) + tools | DSH agent preset JS |
| P2 web plugin | `web-plugin/` | Same-origin `/worktree-api` proxy + tree/session-card UI | DSH web bundle |
| P3 import/seed | `tools/` | `import-agents.py`, `seed-demo.py`, `verify.sh` | stdlib Python |

Everything is stdlib-only (Python `http.server` + `sqlite3`, plus stdlib CLI
tools); there are **no third-party Python deps** to install.

---

## 0. Prerequisites

- Test host **3091**, DSH installed, `DSH_HOME` set (e.g. `/www/dsh/home`).
- `/opt/AstrBot/venv/bin/python3` (Python 3.12+). `node` for the JS checks.
- Port **6271** free (prod `6270` must remain untouched and unbound by this repo).

---

## 1. Install the worktree-server (P0)

```bash
cd /www/deepseek\ harness\ workspace/dsh-workbench
# copy the service to its deploy location
sudo cp -r worktree-server /opt/dsh-workbench-server        # or your APP dir
cd /opt/dsh-workbench-server
```

The server is a single stdlib entry point; run it directly:

```bash
# test deployment: port 6271, db in ./data, token file optional
WORKBENCH_SERVER_PORT=6271 \
WORKBENCH_DATA_DIR=/opt/dsh-workbench-server/data \
/opt/AstrBot/venv/bin/python3 server.py
```

### Env vars

| Var | Default | Meaning |
|---|---|---|
| `WORKBENCH_SERVER_PORT` | `6271` | Listen port (**test 6271**, prod `6270`). |
| `WORKBENCH_DATA_DIR` | `./data` | Where `worktree.db` and the default token file live. |
| `WORKBENCH_API_TOKEN_FILE` | `<data_dir>/api-token` | Bearer token file. Create it to require auth. |
| `WORKBENCH_CONTRACT_BASE` | repo root | Base dir to resolve `contract_ref` (`docs/xxx.md`). |

### systemd unit (recommended)

```ini
# /etc/systemd/system/dsh-workbench.service
[Unit]
Description=dsh-workbench worktree server (test 6271)
After=network.target

[Service]
Environment=WORKBENCH_SERVER_PORT=6271
Environment=WORKBENCH_DATA_DIR=/opt/dsh-workbench-server/data
ExecStart=/opt/AstrBot/venv/bin/python3 /opt/dsh-workbench-server/server.py
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now dsh-workbench
```

### Health check

```bash
curl -s http://127.0.0.1:6271/v1/health   # {"ok":true,"version":"0.1.0","db":"worktree.db"}
```

Auth note: the server rejects any request with an `Origin` header and every
`OPTIONS`, and requires `Authorization: Bearer <token>` if the token file exists
(see `worktree-server/README.md`). The web plugin frontend talks to it only via
the same-origin `/worktree-api` proxy, so the browser never hits the raw port.

---

## 2. Install the agent preset plugin (P1)

The P1 preset plugin is `agent-preset/_worktree-plugin/` (JS only; P1 owns
`plugin-v1.js`). Copy it to DSH's agent preset area and register it as a preset
component:

```bash
PRESETS="${DSH_HOME}/.agent-presets"
mkdir -p "${PRESETS}/workbench"
sudo cp -r agent-preset/_worktree-plugin/plugin-v1.js "${PRESETS}/workbench/"
# plus any preset config (.cordis.yml / settings.yaml) you maintain for the
# workbench preset — keep this out of git if it carries machine-specific paths
```

The plugin exports `name = 'dsh-workbench'` and `inject = ['tools']`; it reads the
worktree node for the current session through the host `/worktree-api` proxy
(order 46 `[WORKTREE]` segment). When enabled in a preset, new sessions that mount
a node carry the ancestor-chain summary automatically.

**Preflight gate (mirror harness-memory-archive AGENTS.md):** before restarting
DSH, run

```bash
cp "${PRESETS}/workbench/plugin-v1.js" /tmp/chk.mjs && node --check /tmp/chk.mjs
node --input-type=module -e "await import('${PRESETS}/workbench/plugin-v1.js')"
```

Either failing means **stop and roll back** — never restart DSH with a broken
preset.

---

## 3. Install the web plugin (P2) as a bundle

The web plugin lives in `web-plugin/` (`index.js` host half + `client.js` browser
half). Install it as a DSH web bundle into the web profile:

```bash
WEB_PROFILE="$(dirname "$DSH_HOME")/profiles/web"   # or check your DSH layout
BUNDLE="${WEB_PROFILE}/node_modules/dsh-workbench"
sudo mkdir -p "${BUNDLE}"
sudo cp web-plugin/index.js web-plugin/client.js "${BUNDLE}/"
# bundle metadata lives in web-plugin/package.json + web-plugin/dsh.patch.yml:
sudo cp web-plugin/package.json web-plugin/dsh.patch.yml "${BUNDLE}/"
```

Register the bundle so the web boot loads it:

```bash
# add "dsh-workbench" to the dsh.profile.bundles array in
# ${WEB_PROFILE}/package.json (idempotent — see helper below)
```

The bundle manifests:

- `package.json` → `exports` `"."` (host `./index.js`), `"./client"` (client.js),
  and `dsh.bundle.patch: "./dsh.patch.yml"`.
- `dsh.patch.yml` → `insert: [{ id: dsh-workbench }]`, which registers the UI
  surface plugin under the `dsh-workbench` namespace.

Then **restart the DSH web process** (do not restart the worktree-server). After
boot, confirm the bundle is present and DSH is healthy:

```bash
curl -s http://127.0.0.1:3081/ | grep -c dsh-workbench
```

> The web plugin never calls the raw worktree port: it exposes `/worktree-api/*`
> (same-origin) in the host half, which strips `Origin`, re-injects the Bearer
> token, and forwards to `WORKBENCH_SERVER_PORT` (6271). This satisfies the
> contract's "reject browser Origin" rule + CORS-free browser access.

---

## 4. Seed the demo

Seed the deepmemory three-layer acceptance sample (root / 主体 /
memory-server + interface + doc + workspace `deepseek-harness`):

```bash
cd /www/deepseek\ harness\ workspace/dsh-workbench
WORKBENCH_SERVER_PORT=6271 /opt/AstrBot/venv/bin/python3 tools/seed-demo.py
# -> [seed] created 200 deepmemory … seeded:5  errors:0
```

Idempotent: re-running updates on 409 instead of failing. Apply it to your own
project tree with:

```bash
WORKBENCH_SERVER_PORT=6271 /opt/AstrBot/venv/bin/python3 \
  tools/import-agents.py --root-path /path/to/project --dry-run   # preview
WORKBENCH_SERVER_PORT=6271 /opt/AstrBot/venv/bin/python3 \
  tools/import-agents.py --root-path /path/to/project
```

Verify the tree was written:

```bash
curl -s 'http://127.0.0.1:6271/v1/worktree/tree?root=deepmemory&depth=3'
curl -s http://127.0.0.1:6271/v1/worktree/ancestors/deepmemory/主体/memory-server
```

---

## 5. Verification checklist

```bash
cd /www/deepseek\ harness\ workspace/dsh-workbench
bash tools/verify.sh      # py_compile + node --check + ESM smoke + P0 tests
curl -s http://127.0.0.1:6271/v1/health
WORKBENCH_SERVER_PORT=6271 /opt/AstrBot/venv/bin/python3 tools/seed-demo.py
```

Also, per the repo preflight: after installing the preset plugin, load a new
session that mounts a node and confirm the `[WORKTREE]` segment appears within
the ≤500-token budget, and that it is frozen within the turn (TTL 300s).

---

## 6. Production (6270) — out of scope during development

- The production stack (port **6270**) is **never touched** by development work.
- Only after test-machine (3091/6271) verification is complete is a production
  promotion considered — and that is a separate, deliberate deploy.
- If you ever see `/dsh*` production directories referenced in a task, do **not**
  modify them: write only under the workbench repo.

---

## References

- API contract: `docs/api-contract.md`
- Design/ADR: `docs/DESIGN.md`
- Context & module table: `CONTEXT.md`
- Server details: `worktree-server/README.md`
- P1 preset plugin: `agent-preset/_worktree-plugin/README.md`
