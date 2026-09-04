#!/opt/AstrBot/venv/bin/python3
"""dsh-workbench P0: standalone HTTP server for the worktree API.

Serves the endpoints in `docs/api-contract.md` §3 over stdlib
``http.server`` + ``sqlite3``.  Auth follows the memory-server pattern
(§0): reject browser Origin (403), reject OPTIONS (403), require
``Authorization: Bearer <token>`` when the token file is present.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from worktree import ConflictError, NotFoundError, ValidationError, WorktreeStore

API_VERSION = "0.1.0"
DB_NAME = "worktree.db"


class WorkbenchHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler, store, config):
        super().__init__(addr, handler)
        self.store = store
        self.config = config


class Handler(BaseHTTPRequestHandler):
    server_version = "dsh-workbench-worktree/0.1"

    # ---- accessors ------------------------------------------------------

    @property
    def store(self):
        return self.server.store

    @property
    def config(self):
        return self.server.config

    def log_message(self, fmt, *args):
        print("[http]", self.address_string(), fmt % args, flush=True)

    # ---- low-level helpers ---------------------------------------------

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reject(self):
        """Contract §0: reject browser Origin and missing/mismatched token."""
        if self.headers.get("Origin"):
            self._send(403, {"error": "browser origin is not allowed"})
            return True
        token_file = self.config.get("token_file")
        token = ""
        if token_file and os.path.exists(token_file):
            try:
                with open(token_file, encoding="utf-8") as fh:
                    token = fh.read().strip()
            except OSError:
                token = ""
        # If a token file is configured, the bearer token is mandatory.
        if token and self.headers.get("Authorization") != "Bearer " + token:
            self._send(403, {"error": "bearer token required"})
            return True
        return False

    def _read_body(self):
        raw = self.headers.get("Content-Length")
        if not raw:
            return {}
        try:
            length = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError("invalid Content-Length") from exc
        if length < 0:
            raise ValidationError("invalid Content-Length")
        if length == 0:
            return {}
        payload = self.rfile.read(length)
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except (ValueError, TypeError) as exc:
            raise ValidationError("invalid JSON body") from exc

    def _path_parts(self):
        parsed = urlparse(self.path)
        parts = [unquote(s) for s in parsed.path.split("/") if s != ""]
        return parts, parse_qs(parsed.query)

    def _node_path(self, parts):
        """Reassemble the `<path>` captured after the fixed prefix."""
        return "/".join(parts[3:])

    # ---- error dispatch ------------------------------------------------

    def _run(self, fn):
        try:
            return fn()
        except NotFoundError as exc:
            return self._send(404, {"error": str(exc)})
        except ConflictError as exc:
            return self._send(409, {"error": str(exc)})
        except ValidationError as exc:
            return self._send(400, {"error": str(exc)})
        except Exception as exc:  # 500 internal
            return self._send(500, {"error": "internal error"})

    # ---- OPTIONS --------------------------------------------------------

    def do_OPTIONS(self):
        self._send(403, {"error": "browser origin is not allowed"})

    # ---- GET ------------------------------------------------------------

    def do_GET(self):
        if self._reject():
            return
        self._run(self._handle_get)

    def _handle_get(self):
        parts, qs = self._path_parts()

        if parts == ["v1", "health"]:
            return self._send(
                200, {"ok": True, "version": API_VERSION, "db": DB_NAME}
            )

        if parts == ["v1", "worktree", "nodes"]:
            workspace = qs.get("workspace", [None])[0]
            return self._send(200, {"nodes": self.store.list_nodes(workspace)})

        if parts[:3] == ["v1", "worktree", "nodes"] and len(parts) >= 4:
            path = self._node_path(parts)
            node = self.store.get_node(path)
            if node is None:
                return self._send(404, {"error": "node not found"})
            return self._send(200, {"node": node, "links": self.store.get_node_links(path)})

        if parts == ["v1", "worktree", "tree"]:
            root = qs.get("root", [None])[0]
            depth = qs.get("depth", [None])[0]
            workspace = qs.get("workspace", [None])[0]
            return self._send(200, self.store.get_tree(root, depth, workspace))

        if parts[:3] == ["v1", "worktree", "ancestors"] and len(parts) >= 4:
            path = self._node_path(parts)
            ancestors = self.store.get_ancestors(path)
            return self._send(200, {"path": path, "ancestors": ancestors})

        if parts[:3] == ["v1", "worktree", "contract"] and len(parts) >= 4:
            path = self._node_path(parts)
            return self._send(200, self.store.get_contract(path))

        if parts == ["v1", "worktree", "session-link"]:
            return self._send(400, {"error": "session-link requires POST/DELETE"})

        if parts[:3] == ["v1", "worktree", "session"] and len(parts) >= 4:
            session_id = "/".join(parts[3:])
            return self._send(200, self.store.get_session(session_id))

        if parts == ["v1", "worktree", "import"]:
            return self._send(400, {"error": "import requires POST"})

        return self._send(404, {"error": "not found"})

    # ---- POST -----------------------------------------------------------

    def do_POST(self):
        if self._reject():
            return
        self._run(self._handle_post)

    def _handle_post(self):
        parts, _qs = self._path_parts()

        if parts == ["v1", "worktree", "nodes"]:
            body = self._read_body()
            node = self.store.create_node(
                path=body.get("path"),
                parent_path=body.get("parent_path"),
                kind=body.get("kind", "component"),
                name=body.get("name"),
                desc=body.get("desc"),
                contract_ref=body.get("contract_ref"),
                meta=body.get("meta"),
                workspace=body.get("workspace"),
            )
            return self._send(200, {"node": node})

        if parts[:3] == ["v1", "worktree", "nodes"] and len(parts) >= 4:
            path = self._node_path(parts)
            node = self.store.update_node(path, self._read_body())
            return self._send(200, {"node": node})

        if parts == ["v1", "worktree", "session-link"]:
            body = self._read_body()
            node_path = body.get("node_path")
            session_id = body.get("session_id")
            if not node_path or not session_id:
                return self._send(
                    400, {"error": "node_path and session_id are required"}
                )
            return self._send(200, self.store.set_session_link(node_path, session_id))

        if parts == ["v1", "worktree", "import"]:
            body = self._read_body()
            return self._send(
                200,
                self.store.import_nodes(
                    source=body.get("source"),
                    root_path=body.get("root_path"),
                    workspace=body.get("workspace"),
                    nodes=body.get("nodes"),
                ),
            )

        return self._send(404, {"error": "not found"})

    # ---- DELETE ---------------------------------------------------------

    def do_DELETE(self):
        if self._reject():
            return
        self._run(self._handle_delete)

    def _handle_delete(self):
        parts, _qs = self._path_parts()

        if parts[:3] == ["v1", "worktree", "nodes"] and len(parts) >= 4:
            path = self._node_path(parts)
            return self._send(200, self.store.delete_node(path))

        if parts == ["v1", "worktree", "session-link"]:
            body = self._read_body()
            session_id = body.get("session_id")
            if not session_id:
                return self._send(400, {"error": "session_id is required"})
            return self._send(200, self.store.remove_session_link(session_id))

        return self._send(404, {"error": "not found"})


def create_server(data_dir, port, token_file=None, contract_base=None):
    """Build a fully-configured WorkbenchHTTPServer (used by main and tests)."""
    os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, DB_NAME)
    store = WorktreeStore(db_path, contract_base=contract_base)
    if token_file is None:
        token_file = os.path.join(data_dir, "api-token")
    config = {"token_file": token_file}
    return WorkbenchHTTPServer(("localhost", port), Handler, store, config)


def main():
    port = int(os.environ.get("WORKBENCH_SERVER_PORT", "6271"))
    here = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.environ.get("WORKBENCH_DATA_DIR") or os.path.join(here, "data")
    contract_base = os.environ.get("WORKBENCH_CONTRACT_BASE") or os.path.dirname(here)
    token_file = os.environ.get("WORKBENCH_API_TOKEN_FILE")
    srv = create_server(data_dir, port, token_file=token_file, contract_base=contract_base)
    print(
        "dsh-workbench-worktree listening on http://localhost:%d (db=%s)"
        % (port, os.path.join(data_dir, DB_NAME)),
        flush=True,
    )
    srv.serve_forever()


if __name__ == "__main__":
    main()
