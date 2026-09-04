"""dsh-workbench P0: sqlite3 store for the worktree.

Implements the data model and node-serialization contract from
`docs/api-contract.md` §§1-2.  Pure stdlib sqlite3, no third-party deps,
no coupling to deepmemory / dsh-longlongchat.

The store owns a single sqlite3 connection guarded by an ``threading.RLock``
(the server runs a ``ThreadingHTTPServer``, so many threads share it; using a
re-entrant lock lets store helpers call each other safely).
"""

import json
import os
import sqlite3
import threading
import time

# The distinct node kinds allowed by the contract §1 CHECK constraint.
KINDS = ("root", "component", "service", "interface", "contract", "doc")

# The distinct node_links kinds allowed by the contract §1 CHECK constraint.
LINK_KINDS = ("contract", "session", "memory")

DB_FILENAME = "worktree.db"


class WorktreeError(Exception):
    """Base error for this module."""


class ValidationError(WorktreeError):
    """400 - bad argument / bad type / bad enum."""


class NotFoundError(WorktreeError):
    """404 - node or link not found."""


class ConflictError(WorktreeError):
    """409 - path already exists."""


def install_schema(conn):
    """Create the two tables (and indexes) from api-contract §1."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS worktree_nodes (
          id           INTEGER PRIMARY KEY AUTOINCREMENT,
          path         TEXT UNIQUE NOT NULL,
          parent_path  TEXT,
          kind         TEXT NOT NULL CHECK(kind IN ('root','component','service','interface','contract','doc')),
          name         TEXT NOT NULL,
          desc         TEXT,
          contract_ref TEXT,
          meta         TEXT,
          created_at   REAL NOT NULL,
          updated_at   REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_parent ON worktree_nodes(parent_path)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS node_links (
          node_id    INTEGER NOT NULL,
          kind       TEXT NOT NULL CHECK(kind IN ('contract','session','memory')),
          ref        TEXT NOT NULL,
          created_at REAL NOT NULL,
          UNIQUE(node_id, kind, ref)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_links_node     ON node_links(node_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_links_kind_ref ON node_links(kind, ref)"
    )
    conn.commit()


class WorktreeStore:
    """A thin, thread-safe wrapper over the worktree sqlite schema."""

    def __init__(self, db_path, contract_base=None):
        self.db_path = db_path
        self.lock = threading.RLock()
        is_memory = db_path == ":memory:" or db_path.startswith("file::memory:")
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if not is_memory:
            # WAL gives better concurrency for the threaded HTTP server.
            self.conn.execute("PRAGMA journal_mode=WAL")
        install_schema(self.conn)
        # Base directory used to resolve `contract_ref` (``docs/xxx.md``) into a
        # real file for the contract endpoint.
        if contract_base is None:
            contract_base = os.path.dirname(os.path.abspath(db_path))
        self.contract_base = contract_base

    def close(self):
        with self.lock:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass

    # ------------------------------------------------------------ helpers

    def _now(self):
        return time.time()

    def _row_to_node(self, row):
        """Serialize a DB row into the contract §2 node shape."""
        meta = {}
        if row["meta"]:
            try:
                meta = json.loads(row["meta"])
            except (ValueError, TypeError):
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
        return {
            "id": row["id"],
            "path": row["path"],
            "parent_path": row["parent_path"],
            "kind": row["kind"],
            "name": row["name"],
            "desc": row["desc"],
            "contract_ref": row["contract_ref"],
            "meta": meta,
            # Redundant top-level `workspace` = meta.workspace (contract §2).
            "workspace": meta.get("workspace"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _get_node_row(self, path):
        return self.conn.execute(
            "SELECT * FROM worktree_nodes WHERE path = ?", (path,)
        ).fetchone()

    def _get_node_by_id(self, node_id):
        row = self.conn.execute(
            "SELECT * FROM worktree_nodes WHERE id = ?", (node_id,)
        ).fetchone()
        return self._row_to_node(row) if row else None

    def _get_links(self, node_id):
        rows = self.conn.execute(
            "SELECT node_id, kind, ref, created_at FROM node_links WHERE node_id = ?",
            (node_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ node CRUD

    def create_node(
        self,
        path,
        parent_path=None,
        kind="component",
        name=None,
        desc=None,
        contract_ref=None,
        meta=None,
        workspace=None,
    ):
        """Create a node; raises ConflictError if ``path`` already exists."""
        if not path or not isinstance(path, str):
            raise ValidationError("path is required")
        if kind not in KINDS:
            raise ValidationError("invalid kind: %s" % kind)
        m = dict(meta) if isinstance(meta, dict) else {}
        if workspace is not None:
            m["workspace"] = workspace
        if name is None:
            name = path.split("/")[-1]
        now = self._now()
        with self.lock:
            try:
                self.conn.execute(
                    """
                    INSERT INTO worktree_nodes
                      (path, parent_path, kind, name, desc, contract_ref,
                       meta, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        path,
                        parent_path,
                        kind,
                        name,
                        desc,
                        contract_ref,
                        json.dumps(m, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                self.conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ConflictError("node path exists") from exc
            return self.get_node(path)

    def get_node(self, path):
        """Return the node dict for ``path`` or None."""
        with self.lock:
            row = self._get_node_row(path)
            return self._row_to_node(row) if row else None

    def get_node_links(self, path):
        """Return the links list for the node at ``path`` (empty if missing)."""
        with self.lock:
            row = self._get_node_row(path)
            if row is None:
                raise NotFoundError(path)
            return self._get_links(row["id"])

    def list_nodes(self, workspace=None):
        """List all nodes; optionally filter by ``meta.workspace``."""
        with self.lock:
            rows = self.conn.execute("SELECT * FROM worktree_nodes").fetchall()
            out = []
            for row in rows:
                node = self._row_to_node(row)
                if workspace is not None and node["workspace"] != workspace:
                    continue
                out.append(node)
            return out

    def update_node(self, path, data):
        """Patch an existing node (contract §3.2 POST /nodes/<path>)."""
        with self.lock:
            row = self._get_node_row(path)
            if row is None:
                raise NotFoundError(path)
            m = json.loads(row["meta"]) if row["meta"] else {}
            if not isinstance(m, dict):
                m = {}

            updates = {}
            if "name" in data:
                updates["name"] = data["name"]
            if "desc" in data:
                updates["desc"] = data["desc"]
            if "contract_ref" in data:
                updates["contract_ref"] = data["contract_ref"]
            if "parent_path" in data:
                updates["parent_path"] = data["parent_path"]
            if "kind" in data:
                if data["kind"] not in KINDS:
                    raise ValidationError("invalid kind: %s" % data["kind"])
                updates["kind"] = data["kind"]

            merged_meta = dict(m)
            if isinstance(data.get("meta"), dict):
                merged_meta.update(data["meta"])
            if data.get("workspace") is not None:
                merged_meta["workspace"] = data["workspace"]
            updates["meta"] = json.dumps(merged_meta, ensure_ascii=False)
            updates["updated_at"] = self._now()

            set_clause = ", ".join("%s = ?" % k for k in updates)
            values = [updates[k] for k in updates] + [path]
            self.conn.execute(
                "UPDATE worktree_nodes SET %s WHERE path = ?" % set_clause, values
            )
            self.conn.commit()
            return self.get_node(path)

    def delete_node(self, path):
        """Delete a node and its links (contract §3.2 DELETE /nodes/<path>)."""
        with self.lock:
            node = self.get_node(path)
            if node is None:
                raise NotFoundError(path)
            linked = self.conn.execute(
                "SELECT COUNT(*) FROM node_links WHERE node_id = ?", (node["id"],)
            ).fetchone()[0]
            self.conn.execute("DELETE FROM node_links WHERE node_id = ?", (node["id"],))
            self.conn.execute("DELETE FROM worktree_nodes WHERE id = ?", (node["id"],))
            self.conn.commit()
            return {"ok": True, "deleted": 1, "links": linked}

    # ------------------------------------------------------------ tree

    def get_tree(self, root_path=None, depth=1, workspace=None):
        """Return the tree (contract §3.3).

        ``depth`` is how many levels of children to walk below the root / below
        each returned root node.  With no ``root_path`` the forest of root
        nodes (parent_path empty) is returned with ``root`` null.
        """
        try:
            depth = int(depth)
        except (TypeError, ValueError):
            depth = 1
        if depth < 0:
            depth = 0
        with self.lock:
            nodes = self.list_nodes(workspace)
            by_path = {n["path"]: n for n in nodes}
            children_map = {}
            for n in nodes:
                children_map.setdefault(n["parent_path"], []).append(n)
            for key in children_map:
                children_map[key].sort(key=lambda x: x["path"])

            def build(node, remaining):
                item = dict(node)
                if remaining > 0:
                    item["children"] = [
                        build(c, remaining - 1)
                        for c in children_map.get(node["path"], [])
                    ]
                else:
                    item["children"] = []
                return item

            if root_path:
                root = by_path.get(root_path)
                if root is None:
                    raise NotFoundError(root_path)
                return {
                    "root": root,
                    "children": [
                        build(c, depth - 1)
                        for c in children_map.get(root["path"], [])
                    ],
                }
            # Forest of all root nodes (parent_path empty / null).
            roots = [n for n in nodes if not n["parent_path"]]
            roots.sort(key=lambda x: x["path"])
            return {
                "root": None,
                "children": [build(r, depth - 1) for r in roots],
            }

    # ------------------------------------------------------------ ancestors

    def get_ancestors(self, path):
        """Return the ancestor chain root->node, including ``path`` itself.

        Raises NotFoundError if the node does not exist.
        """
        with self.lock:
            node = self.get_node(path)
            if node is None:
                raise NotFoundError(path)
            chain = []
            seen = set()
            cur = node
            while cur is not None and cur["path"] not in seen:
                seen.add(cur["path"])
                chain.append(cur)
                cur = self.get_node(cur["parent_path"]) if cur["parent_path"] else None
            chain.reverse()  # root -> ... -> node
            return chain

    # ------------------------------------------------------------ contract

    def get_contract(self, path):
        """Return the contract payload (contract §3.5)."""
        with self.lock:
            node = self.get_node(path)
            if node is None:
                raise NotFoundError(path)
            ref = node["contract_ref"]
            cpath = None
            if ref:
                cpath = os.path.abspath(os.path.join(self.contract_base, ref))
            text = None
            if cpath and os.path.isfile(cpath):
                try:
                    with open(cpath, "r", encoding="utf-8") as fh:
                        text = fh.read()
                except OSError:
                    text = None
            links = self._get_links(node["id"])
            return {
                "node": node,
                "contract_ref": ref,
                "contract_text": text,
                "contract_path": cpath,
                "links": links,
            }

    # ------------------------------------------------------------ session links

    def set_session_link(self, node_path, session_id):
        """Upsert (idempotent) a session link (contract §3.6)."""
        with self.lock:
            node = self.get_node(node_path)
            if node is None:
                raise NotFoundError(node_path)
            now = self._now()
            self.conn.execute(
                """
                INSERT INTO node_links (node_id, kind, ref, created_at)
                VALUES (?, 'session', ?, ?)
                ON CONFLICT(node_id, kind, ref) DO UPDATE SET created_at = excluded.created_at
                """,
                (node["id"], session_id, now),
            )
            self.conn.commit()
            return {"ok": True, "node_path": node_path, "session_id": session_id}

    def get_session(self, session_id):
        """Resolve a session id to its node + ancestors (contract §3.6)."""
        with self.lock:
            row = self.conn.execute(
                "SELECT node_id FROM node_links WHERE kind = 'session' AND ref = ? LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is None:
                return {"node_path": None, "node": None, "ancestors": []}
            node = self._get_node_by_id(row["node_id"])
            if node is None:
                return {"node_path": None, "node": None, "ancestors": []}
            ancestors = self.get_ancestors(node["path"])
            return {"node_path": node["path"], "node": node, "ancestors": ancestors}

    def remove_session_link(self, session_id):
        """Remove the session link for ``session_id`` (idempotent)."""
        with self.lock:
            cur = self.conn.execute(
                "DELETE FROM node_links WHERE kind = 'session' AND ref = ?",
                (session_id,),
            )
            self.conn.commit()
            return {"ok": True, "deleted": cur.rowcount}

    # ------------------------------------------------------------ import

    def import_nodes(self, source, root_path=None, workspace=None, nodes=None):
        """Create a batch of nodes (contract §3.7).

        ``source`` must be ``agents`` or ``frontmatter``.  ``nodes`` carries the
        actual node dicts to materialise (the parsing of AGENTS.md / frontmatter
        belongs to P3; this store just persists what it is given).
        """
        if source not in ("agents", "frontmatter"):
            raise ValidationError("invalid import source: %s" % source)
        created = []
        for item in nodes or []:
            if not isinstance(item, dict):
                raise ValidationError("each imported node must be an object")
            path = item.get("path")
            if not path:
                raise ValidationError("each imported node requires a path")
            node = self.create_node(
                path=path,
                parent_path=item.get("parent_path"),
                kind=item.get("kind", "component"),
                name=item.get("name"),
                desc=item.get("desc"),
                contract_ref=item.get("contract_ref"),
                meta=item.get("meta"),
                workspace=item.get("workspace", workspace),
            )
            created.append(node)
        return {"imported": len(created), "nodes": created}
