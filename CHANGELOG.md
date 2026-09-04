# Changelog

All notable changes to dsh-workbench are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- **P0** `worktree-server/` — stdlib Python service: `worktree.py` (SQLite store:
  schema §1, node serialization §2, CRUD / tree / ancestors / contract /
  session-link / import) + `server.py` (HTTP server + auth per contract §0) +
  `tests/test_worktree.py`.
- **P1** `agent-preset/_worktree-plugin/plugin-v1.js` — preset plugin: inherited
  injection `[WORKTREE]` (order 46, ≤500 token, TTL 300s) + tools
  (`worktree_descendants` / `worktree_ancestors` / `worktree_contract`).
- **P2** `web-plugin/` — DSH client plugin: host half `/worktree-api` same-origin
  proxy + settings namespace; client half tree + session-card view.
- **P3** `tools/import-agents.py` — import a directory tree + `AGENTS.md`
  frontmatter/headings into the worktree service (`--root-path`, `--url`,
  `--token-file`, `--dry-run`; create/`409`→update; `imported:N` summary).
- **P3** `tools/seed-demo.py` — idempotent deepmemory three-layer acceptance
  sample (root / 主体 / memory-server + interface + doc + workspace
  `deepseek-harness`).
- **P3** `tools/verify.sh` — repo gate: `py_compile` on `worktree-server/*.py` and
  `tools/*.py`, `node --check` on the preset + web plugin JS, ESM load smoke on
  the preset plugin, and the P0 test suite; prints PASS/SKIP/FAIL per step and
  exits non-zero on any FAIL.
- **Docs** — `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `docs/INSTALL.md`,
  `docs/DESIGN.md`, `docs/api-contract.md`, `CONTEXT.md`, and
  `agent-preset/IMPLEMENTATION.md`.

### Notes
- Two-phase iron rule: test machine (3091 / **6271**) only during development;
  production (**6270**) is not touched.
- `verify.sh` reports **SKIP** (not failure) for a step whose input artifact is
  not yet present in this checkout because a sibling module that owns it (P0
  code/test, P1 plugin JS, P2 web plugin) has not landed; **FAIL** is reserved
  for a present artifact that genuinely broke its check.
