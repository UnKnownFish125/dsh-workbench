#!/opt/AstrBot/venv/bin/python3
"""dsh-workbench P3 — directory-tree / AGENTS.md importer.

Reads a project directory tree (plus optional ``AGENTS.md`` files and their
frontmatter), derives ``worktree`` nodes (``kind`` / ``parent_path`` / ``name``
/ ``desc`` / ``contract_ref`` / ``meta``), and POSTs them to the dsh-workbench
service so the project structure, interfaces and contracts become programmatically
readable (the non-linear workbench tree).

Contract (docs/api-contract.md §1/§3.2):
    POST /v1/worktree/nodes        -> 200 {"node": {...}} | 409 "node path exists"
    POST /v1/worktree/nodes/<path> -> 200 {"node": {...}}   (update, used on 409)

``<path>`` in the URL is whole-segment encoded (``encodeURIComponent``), i.e.
``deepmemory/memory-server`` -> ``deepmemory%2Fmemory-server`` (the server decodes
and matches the full path, the node primary key).

Deliberately stdlib-only: argparse / json / os / re / sys / pathlib / urllib.
No third-party dependencies.

Examples
--------
    # read ./repo, POST to the test server (port from env or default 6271)
    python3 tools/import-agents.py --root-path ./repo

    # never touch the server, just print what would be sent
    python3 tools/import-agents.py --root-path ./repo --dry-run

    # explicit server + bearer token from a file
    python3 tools/import-agents.py --root-path ./repo \
        --url http://127.0.0.1:6271 --token-file data/api-token
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# The only kinds the worktree service accepts (api-contract §1).
VALID_KINDS = ("root", "component", "service", "interface", "contract", "doc")

# Directories we never turn into nodes (VCS / deps / build / runtime artifacts).
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "dist", "build", "out",
    "__pycache__", ".venv", "venv", ".tmp", ".cache", ".pytest_cache",
    "data",  # runtime database dirs (worktree.db etc.) are not structure
}

# Literal markers used by the kind heuristics (matched on name+filename).
SERVICE_MARKERS = ("service", "server", "backend", "svc", "daemon")
COMPONENT_MARKERS = ("frontend", "client", "ui", "web", "plugin", "preset")
INTERFACE_MARKERS = ("interface", "endpoint", "api", "rpc")
CONTRACT_MARKERS = ("contract", "api_contract", "契约", "api-contract")
DOC_MARKERS = ("doc", "readme", "guide", "manual", "文档", "指南", "手册")

# Files we consider "structure relevant" (everything else is ignored so the
# tree stays a clean structure/contract view instead of a full source listing).
INCLUDE_FILE_EXTENSIONS = (".md", ".markdown")


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------
def quote_path(path: str) -> str:
    """Whole-segment URL-encode a node path (slash escapes to %2F)."""
    return urllib.parse.quote(path, safe="")


def base_url(args) -> str:
    """Resolve the service base URL from --url or WORKBENCH_SERVER_PORT."""
    if getattr(args, "url", None):
        return args.url.rstrip("/")
    port = int(os.environ.get("WORKBENCH_SERVER_PORT", "6271"))
    return "http://127.0.0.1:{}".format(port)


def read_token(token_file: str | None) -> str | None:
    """Read a bearer token from a file. Returns None when absent/unreadable."""
    if not token_file:
        return None
    try:
        with open(token_file, "r", encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def request_json(url: str, payload: dict, token: str | None,
                 method: str = "POST") -> tuple[int, dict]:
    """POST/… JSON to ``url``; returns (http_status, parsed_json)."""
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
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


def upsert_node(base: str, token: str | None, node: dict) -> tuple[str, int]:
    """Create a node; on 409 (path exists) update via POST /nodes/<path>.

    Returns (action, http_status).
    """
    create_url = base + "/v1/worktree/nodes"
    status, body = request_json(create_url, node, token)
    if status == 409:
        # path already exists -> update (create/update are split in the contract)
        update_url = base + "/v1/worktree/nodes/" + quote_path(node["path"])
        status, body = request_json(update_url, node, token)
        return "updated", status
    return "created", status


# --------------------------------------------------------------------------
# Kind heuristics
# --------------------------------------------------------------------------
def guess_kind(name: str, filename: str, is_dir: bool) -> str:
    """Best-effort kind for a node based on its name / filename / label.

    Order matters: explicit 'interface' / 'contract' labels win over generic
    service/component heuristics so a doc named ``api-contract`` is never
    mislabeled a service.
    """
    n = "{} {}".format(name, filename).lower()
    if any(m in n for m in CONTRACT_MARKERS):
        return "contract"
    if any(m in n for m in INTERFACE_MARKERS):
        return "interface"
    if any(m in n for m in SERVICE_MARKERS):
        return "service"
    if any(m in n for m in COMPONENT_MARKERS):
        return "component"
    if not is_dir and filename.lower().endswith(INCLUDE_FILE_EXTENSIONS):
        if any(m in n for m in DOC_MARKERS):
            return "doc"
        return "doc"
    return "component"


def slugify(name: str) -> str:
    """Turn an arbitrary heading string into a stable path segment."""
    slug = re.sub(r"\s+", "-", name.strip().lower())
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff\-_.]", "", slug)
    return slug or "node"


# --------------------------------------------------------------------------
# Frontmatter (lightweight, stdlib-only) — no YAML engine on purpose
# --------------------------------------------------------------------------
def split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body) for ``---\\n...\\n---`` blocks.

    We only need a handful of scalar keys (kind / desc / workspace /
    contract_ref) and an optional ``nodes:`` list, so we parse the leading
    block with a tolerant line scanner instead of pulling in pyyaml.
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    fm: dict = {}
    for line in lines[1:end]:
        line = line.rstrip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key in ("kind", "desc", "workspace", "contract_ref", "name"):
            fm[key] = val
    return fm, "\n".join(lines[end + 1:])


def parse_root_heading(body: str) -> str:
    """Pull the first H1 (project title) as the root node description source."""
    for line in body.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    return ""


def parse_level2_headings(body: str) -> list[str]:
    """Extract ``## Section`` headings (skipping ``###``+ and fenced code)."""
    out: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^##\s+(.+)$", line.strip())
        if m:
            out.append(m.group(1).strip())
    return out


# --------------------------------------------------------------------------
# Node builder
# --------------------------------------------------------------------------
def build_nodes(root_path: str, workspace: str | None) -> list[dict]:
    """Walk ``root_path`` and return an ordered list of worktree node dicts.

    Two sources feed the tree:
      1. directory skeleton (directories -> nodes), and
      2. markdown docs / AGENTS.md headings -> component/doc/contract/interfaces.

    Node ``path`` values are root-relative and use ``/``; the root node is the
    basename of ``root_path`` with parent_path=None (kind='root').
    """
    abs_root = os.path.abspath(root_path)
    root_name = os.path.basename(abs_root) or "root"
    nodes: dict[str, dict] = {}
    order: list[str] = []

    def add(node: dict) -> None:
        p = node["path"]
        if p not in nodes:
            order.append(p)
            nodes[p] = node
        else:
            # merge: keep the more specific kind/desc; never overwrite a
            # non-default kind with a default one
            cur = nodes[p]
            cur_kind_default = cur["kind"] in ("component", "doc")
            nk = node["kind"]
            if nk != "component" or cur_kind_default:
                if nk not in ("component", "doc") or cur["kind"] in ("component",):
                    cur["kind"] = nk
            for k in ("desc", "contract_ref"):
                if node.get(k) and not cur.get(k):
                    cur[k] = node[k]
            if node.get("meta"):
                cur["meta"] = {**cur.get("meta", {}), **node["meta"]}

    def mk(path: str, parent: str | None, kind: str, name: str,
           desc: str = "", contract_ref: str | None = None,
           meta: dict | None = None) -> dict:
        node = {
            "path": path,
            "kind": kind,
            "name": name,
            "desc": desc,
        }
        if parent:
            node["parent_path"] = parent
        if contract_ref:
            node["contract_ref"] = contract_ref
        if meta:
            node["meta"] = meta
        if workspace:
            node["workspace"] = workspace
            node.setdefault("meta", {})["workspace"] = workspace
        return node

    # root
    add(mk(root_name, None, "root", root_name, "project root"))

    # ---- directory skeleton ----
    for dirpath, dirnames, filenames in os.walk(abs_root):
        dirnames[:] = [d for d in sorted(dirnames) if d not in SKIP_DIRS]
        rel = os.path.relpath(dirpath, abs_root)
        if rel == ".":
            parts = []
        else:
            parts = rel.replace("\\", "/").split("/")
        parent_path = "/".join([root_name] + parts[:-1]) if parts else None
        node_path = "/".join([root_name] + parts) if parts else root_name
        if parts:
            kind = guess_kind(parts[-1], "", is_dir=True)
            add(mk(node_path, parent_path or None, kind, parts[-1]))

        # markdown files become leaf nodes under their containing directory
        for fname in sorted(filenames):
            if fname.lower().endswith(INCLUDE_FILE_EXTENSIONS):
                stem = os.path.splitext(fname)[0]
                if stem.lower() == "agents":
                    continue  # AGENTS.md handled separately (headings/subtree)
                file_path = "/".join([node_path, stem]) if parts \
                    else "/".join([root_name, stem])
                kind = guess_kind(stem, fname, is_dir=False)
                add(mk(file_path, node_path or None, kind, stem,
                       desc=_md_first_sentence(os.path.join(dirpath, fname))))

    # ---- AGENTS.md: frontmatter + level-2 headings ----
    for dirpath, dirnames, filenames in os.walk(abs_root):
        dirnames[:] = [d for d in sorted(dirnames) if d not in SKIP_DIRS]
        if "AGENTS.md" not in filenames:
            continue
        rel = os.path.relpath(dirpath, abs_root)
        parts = [] if rel == "." else rel.replace("\\", "/").split("/")
        base_path = "/".join([root_name] + parts) if parts else root_name
        fm, body = _read_agents_md(os.path.join(dirpath, "AGENTS.md"))
        if fm.get("kind"):
            if base_path in nodes:
                nodes[base_path]["kind"] = fm["kind"]
        if not parts and fm.get("desc"):
            nodes[root_name]["desc"] = fm["desc"]
        if parts and base_path in nodes and fm.get("desc"):
            nodes[base_path]["desc"] = fm["desc"]
        # `## Section` -> component children of this node
        for heading in parse_level2_headings(body):
            child = mk(
                "/".join([base_path, slugify(heading)]),
                base_path,
                "component",
                heading,
                desc=heading,
            )
            add(child)
    return [nodes[p] for p in order]


def _read_agents_md(path: str) -> tuple[dict, str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}, ""
    fm, body = split_frontmatter(text)
    return fm, body


def _md_first_sentence(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith(">"):
            return line[:160]
    return ""


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def parse_args(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="import-agents.py",
        description="Import a project directory tree + AGENTS.md into the "
                    "dsh-workbench service.",
    )
    p.add_argument("--root-path", required=True,
                   help="Directory tree to import.")
    p.add_argument("--url", default=None,
                   help="Service base URL (default http://127.0.0.1:"
                        "$WORKBENCH_SERVER_PORT — default 6271).")
    p.add_argument("--token-file", default=None,
                   help="File containing the Workbench API bearer token.")
    p.add_argument("--workspace", default=None,
                   help="Workspace label to annotate every node with (meta).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the derived nodes without POSTing anything.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if not os.path.isdir(args.root_path):
        print("ERROR: --root-path is not a directory: {}".format(args.root_path),
              file=sys.stderr)
        return 2

    nodes = build_nodes(args.root_path, args.workspace)

    if args.dry_run:
        print("dry-run: {} node(s) derived from {}".format(len(nodes),
                                                           args.root_path))
        for n in nodes:
            print("  {}  kind={}  parent={}".format(
                n["path"], n["kind"], n.get("parent_path", "-")))
        return 0

    base = base_url(args)
    token = read_token(args.token_file)
    if token:
        print("using bearer token from {}".format(args.token_file), file=sys.stderr)

    ok = 0
    err = 0
    for node in nodes:
        # never send keys the contract doesn't accept (empty meta -> omit)
        payload = {k: v for k, v in node.items() if v not in (None, "", {})}
        try:
            action, status = upsert_node(base, token, payload)
        except RuntimeError as exc:
            print("[{}] ERR {}: {}".format("import", node["path"], exc))
            err += 1
            continue
        if status in (200, 201):
            print("[{}] {} {} ({})".format(action, status, node["path"],
                                           node["kind"]))
            ok += 1
        else:
            print("[{}] ERR {}: HTTP {} {}".format("import", node["path"],
                                                   status,
                                                   payload.get("desc", "")))
            err += 1

    print("imported:{}  errors:{}".format(ok, err))
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
