#!/opt/AstrBot/venv/bin/python3
"""dsh-workbench P3 — seed the acceptance demo tree.

Registers the deepmemory three-layer sample used in the acceptance criteria
(api-contract §3.x / DESIGN.md §验收)::

    deepmemory                                (root)
    └─ 主体                                    (component)
       └─ memory-server                        (service)
          ├─ export-archive                    (interface)
          └─ literature-export-contract        (doc)

plus the ``deepseek-harness`` workspace annotation on every node.

Idempotent: on ``409 (exists)`` it updates via ``POST /v1/worktree/nodes/<path>``
instead of failing, so re-running the demo never duplicates state.

Contract calls (docs/api-contract.md §1/§3.2):
    POST /v1/worktree/nodes        -> 200 | 409 "node path exists"
    POST /v1/worktree/nodes/<path> -> 200 (update, on 409)

Stdlib-only. No third-party dependencies.

Examples
--------
    python3 tools/seed-demo.py                # to 127.0.0.1:6271
    python3 tools/seed-demo.py --dry-run      # print nodes, no POST
    python3 tools/seed-demo.py --url http://127.0.0.1:6271 --token-file data/api-token
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

CONTRACT_REF = "docs/deepmemory-literature-export-contract.md"
WORKSPACE = "deepseek-harness"

# The nodes that make up the acceptance sample, in tree order.
# meta follows the contract's free-form keys: port / path / interfaces /
# deps / status / workspace.
SEED_NODES: list[dict] = [
    {
        "path": "deepmemory",
        "parent_path": None,
        "kind": "root",
        "name": "deepmemory",
        "desc": "deepmemory 长期记忆项目结构根",
        "meta": {"status": "active", "workspace": WORKSPACE},
    },
    {
        "path": "deepmemory/主体",
        "parent_path": "deepmemory",
        "kind": "component",
        "name": "主体",
        "desc": "deepmemory 主模块（记忆后端 + 插件 + 面板）",
        "meta": {"status": "active", "workspace": WORKSPACE},
    },
    {
        "path": "deepmemory/主体/memory-server",
        "parent_path": "deepmemory/主体",
        "kind": "service",
        "name": "memory-server",
        "desc": "语义长期记忆后端服务（Python, systemd :6230）",
        "contract_ref": CONTRACT_REF,
        "meta": {
            "port": 6230,
            "path": "data/memory.db",
            "interfaces": ["export-archive", "get_sources"],
            "status": "active",
            "workspace": WORKSPACE,
        },
    },
    {
        "path": "deepmemory/主体/memory-server/export-archive",
        "parent_path": "deepmemory/主体/memory-server",
        "kind": "interface",
        "name": "export-archive",
        "desc": "带原始对话的归档导出端点（R1：批量 + since 增量 + 脱敏标记）",
        "contract_ref": CONTRACT_REF,
        "meta": {
            "method": "GET",
            "route": "/v1/memories/export-archive",
            "status": "active",
            "workspace": WORKSPACE,
        },
    },
    {
        "path": "deepmemory/主体/memory-server/literature-export-contract",
        "parent_path": "deepmemory/主体/memory-server",
        "kind": "doc",
        "name": "literature-export-contract",
        "desc": "deepmemory-literature-export-contract 契约文档（与 literature 对接）",
        "contract_ref": CONTRACT_REF,
        "meta": {"status": "active", "workspace": WORKSPACE},
    },
]


# --------------------------------------------------------------------------
# HTTP helpers (mirrors import-agents.py; kept standalone for clarity)
# --------------------------------------------------------------------------
def quote_path(path: str) -> str:
    return urllib.parse.quote(path, safe="")


def base_url(args) -> str:
    if getattr(args, "url", None):
        return args.url.rstrip("/")
    port = int(os.environ.get("WORKBENCH_SERVER_PORT", "6271"))
    return "http://127.0.0.1:{}".format(port)


def read_token(token_file: str | None) -> str | None:
    if not token_file:
        return None
    try:
        with open(token_file, "r", encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def request_json(url: str, payload: dict, token: str | None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"error": raw or exc.reason}
    except urllib.error.URLError as exc:
        raise RuntimeError("cannot reach worktree service at {}: {}"
                           .format(url, exc)) from exc


def upsert(base: str, token: str | None, node: dict) -> tuple[str, int]:
    create_url = base + "/v1/worktree/nodes"
    status, _ = request_json(create_url, node, token)
    if status == 409:
        # exists -> update (create/update split in the contract)
        update_url = base + "/v1/worktree/nodes/" + quote_path(node["path"])
        status, _ = request_json(update_url, node, token)
        return "updated", status
    return "created", status


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="seed-demo.py",
        description="Seed the deepmemory acceptance tree into the "
                    "dsh-workbench service (idempotent).",
    )
    p.add_argument("--url", default=None,
                   help="Service base URL (default http://127.0.0.1:"
                        "$WORKBENCH_SERVER_PORT — default 6271).")
    p.add_argument("--token-file", default=None,
                   help="File containing the Workbench API bearer token.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the seed nodes without POSTing anything.")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    if args.dry_run:
        print("dry-run: {} node(s) to seed, workspace={}".format(
            len(SEED_NODES), WORKSPACE))
        for n in SEED_NODES:
            print("  {}  kind={}  parent={}".format(
                n["path"], n["kind"], n.get("parent_path", "-")))
        return 0

    base = base_url(args)
    token = read_token(args.token_file)
    if token:
        print("using bearer token from {}".format(args.token_file), file=sys.stderr)

    ok = 0
    err = 0
    for node in SEED_NODES:
        payload = {k: v for k, v in node.items() if v not in (None, "", {})}
        # annotation is redundant top-level (== meta.workspace) per contract §2
        payload.setdefault("workspace", WORKSPACE)
        try:
            action, status = upsert(base, token, payload)
        except RuntimeError as exc:
            print("[seed] ERR {}: {}".format(node["path"], exc))
            err += 1
            continue
        if status in (200, 201):
            print("[seed] {} {}  {}".format(action, status, node["path"]))
            ok += 1
        else:
            print("[seed] ERR {}: HTTP {}".format(node["path"], status))
            err += 1

    print("seeded:{}  errors:{}  workspace:{}".format(ok, err, WORKSPACE))
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
