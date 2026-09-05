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

# Soft limits applied at the API boundary (security / resource guards).
MAX_PATH_LEN = 512           # max length of a normalized node path / contract_ref
MAX_CONTRACT_BYTES = 1024 * 1024  # max bytes of a contract file we read (~1MB)
MAX_TREE_DEPTH = 50          # max tree levels we will recurse before 400


def normalize_path(p):
    """Return ``p`` if it is a safe, canonical store path (no traversal).

    Rejects non-strings, empty paths, NUL bytes, over-long paths (>512), and
    any path containing an empty / ``.`` / ``..`` segment (which also rules out
    absolute paths, since those start with an empty segment).  Used uniformly
    by create/update and by the API routes so a single encoding governs all
    ``path`` / ``parent_path`` input.
    """
    if p is None:
        raise ValidationError("path is required")
    if not isinstance(p, str):
        raise ValidationError("path must be a string")
    if not p:
        raise ValidationError("path must not be empty")
    if "\x00" in p:
        raise ValidationError("path contains NUL byte")
    if len(p) > MAX_PATH_LEN:
        raise ValidationError("path exceeds %d characters" % MAX_PATH_LEN)
    segments = p.split("/")
    if any(s in ("", ".", "..") for s in segments):
        raise ValidationError("path contains an invalid segment")
    return p


def validate_contract_ref(ref):
    """Validate a ``contract_ref`` so it can never escape ``contract_base``.

    ``None``/empty is allowed (a node may have no contract).  Otherwise ``ref``
    must be a relative path whose segments are all non-empty and not ``.`` /
    ``..``, with no NUL byte and within ``MAX_PATH_LEN``.  Enforced on write
    (create/update/import) so a traversal ref is rejected with 400 up front.
    """
    if not ref:
        return ref
    if not isinstance(ref, str):
        raise ValidationError("contract_ref must be a string")
    if "\x00" in ref:
        raise ValidationError("contract_ref contains NUL byte")
    if os.path.isabs(ref):
        raise ValidationError("contract_ref must be a relative path")
    if any(s in ("", ".", "..") for s in ref.replace("\\", "/").split("/")):
        raise ValidationError("contract_ref contains an invalid segment")
    if len(ref) > MAX_PATH_LEN:
        raise ValidationError(
            "contract_ref exceeds %d characters" % MAX_PATH_LEN
        )
    return ref


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

    def _collect_subtree_paths(self, path):
        """Return all descendant node *paths* of ``path`` (excluding ``path``).

        Walks the actual ``parent_path`` links, so it is correct even if the
        stored hierarchy does not match the path-string prefix (corrupt data).
        """
        rows = self.conn.execute(
            "SELECT path, parent_path FROM worktree_nodes"
        ).fetchall()
        children = {}
        for r in rows:
            children.setdefault(r["parent_path"], []).append(r["path"])
        out = []
        stack = list(children.get(path, []))
        while stack:
            p = stack.pop()
            out.append(p)
            stack.extend(children.get(p, []))
        return out

    def _ensure_parent_ok(self, path, parent_path, batch_paths=None):
        """Validate ``parent_path`` for a node at ``path`` (create/update).

        Enforces: no self-parenting, no cycle (parent must not be a descendant
        of the node), and that the referenced parent exists.  ``batch_paths``
        may be a set of paths being created together (import), in which case a
        parent may exist within that batch rather than in the DB.
        """
        if not parent_path:
            return
        if parent_path == path:
            raise ValidationError(
                "cycle: a node cannot be its own parent (%s)" % path
            )
        if parent_path in self._collect_subtree_paths(path):
            raise ValidationError(
                "cycle: parent %r is a descendant of %r" % (parent_path, path)
            )
        if batch_paths is not None:
            if parent_path in batch_paths:
                return
            if self.get_node(parent_path) is None:
                raise NotFoundError("parent node not found: %s" % parent_path)
            return
        if self.get_node(parent_path) is None:
            raise NotFoundError("parent node not found: %s" % parent_path)

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
        path = normalize_path(path)
        parent_path = normalize_path(parent_path) if parent_path else None
        contract_ref = validate_contract_ref(contract_ref)
        if kind not in KINDS:
            raise ValidationError("invalid kind: %s" % kind)
        m = dict(meta) if isinstance(meta, dict) else {}
        if workspace is not None:
            m["workspace"] = workspace
        if name is None:
            name = path.split("/")[-1]
        now = self._now()
        with self.lock:
            # A node's parent must already exist and must not introduce a cycle.
            self._ensure_parent_ok(path, parent_path)
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
        path = normalize_path(path)
        if data is None or not isinstance(data, dict):
            raise ValidationError("update body must be a JSON object")
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
                updates["contract_ref"] = validate_contract_ref(data["contract_ref"])
            if "parent_path" in data:
                new_parent = data["parent_path"]
                if new_parent:
                    new_parent = normalize_path(new_parent)
                updates["parent_path"] = new_parent
                # Moving a node must not create a cycle or point at a missing node.
                self._ensure_parent_ok(path, new_parent)
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
        """Delete a node and its subtree (cascade), plus all their links.

        Contract §3.2 DELETE /nodes/<path> deletes the node and its links; to
        keep the tree consistent the whole subtree under ``path`` is removed.
        """
        path = normalize_path(path)
        with self.lock:
            node = self.get_node(path)
            if node is None:
                raise NotFoundError(path)
            # Collect the node id plus every descendant id (subtree).
            ids = [node["id"]]
            for p in self._collect_subtree_paths(path):
                r = self._get_node_row(p)
                if r is not None:
                    ids.append(r["id"])
            placeholders = ",".join("?" for _ in ids)
            cur = self.conn.execute(
                "DELETE FROM node_links WHERE node_id IN (%s)" % placeholders, ids
            )
            linked = cur.rowcount
            cur = self.conn.execute(
                "DELETE FROM worktree_nodes WHERE id IN (%s)" % placeholders, ids
            )
            deleted = cur.rowcount
            self.conn.commit()
            return {"ok": True, "deleted": deleted, "links": linked}

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
        if depth > MAX_TREE_DEPTH:
            raise ValidationError("tree depth exceeds %d" % MAX_TREE_DEPTH)
        if root_path:
            root_path = normalize_path(root_path)
        with self.lock:
            nodes = self.list_nodes(workspace)
            by_path = {n["path"]: n for n in nodes}
            children_map = {}
            for n in nodes:
                children_map.setdefault(n["parent_path"], []).append(n)
            for key in children_map:
                children_map[key].sort(key=lambda x: x["path"])

            def build(node, remaining, stack):
                if node["path"] in stack:
                    raise ConflictError(
                        "tree cycle detected at node %r" % node["path"]
                    )
                if len(stack) >= MAX_TREE_DEPTH:
                    raise ValidationError(
                        "tree depth exceeds %d" % MAX_TREE_DEPTH
                    )
                item = dict(node)
                next_stack = stack + [node["path"]]
                if remaining > 0:
                    item["children"] = [
                        build(c, remaining - 1, next_stack)
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
                        build(c, depth - 1, [])
                        for c in children_map.get(root["path"], [])
                    ],
                }
            # Forest of all root nodes (parent_path empty / null).
            roots = [n for n in nodes if not n["parent_path"]]
            roots.sort(key=lambda x: x["path"])
            return {
                "root": None,
                "children": [build(r, depth - 1, []) for r in roots],
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

    def _resolve_contract_ref(self, ref):
        """Resolve ``ref`` to a real path strictly inside ``contract_base``.

        Returns ``(abs_path, display_path)`` or ``(None, None)``.  A ``ref``
        that is absolute, traverses upward with ``..``/``.``, or contains a NUL
        byte is rejected outright (no file is ever opened).  ``display_path`` is
        base-relative so the API never leaks an internal absolute path.
        """
        if not ref:
            return None, None
        if "\x00" in ref:
            return None, None
        if os.path.isabs(ref):
            return None, None
        if any(s in ("", ".", "..") for s in ref.replace("\\", "/").split("/")):
            return None, None
        base = os.path.realpath(self.contract_base)
        candidate = os.path.realpath(os.path.join(base, ref))
        try:
            common = os.path.commonpath([base, candidate])
        except ValueError:
            return None, None
        if common != base:
            return None, None
        return candidate, os.path.relpath(candidate, base)

    def _read_contract_file(self, path):
        """Read a contract file, capping at ~``MAX_CONTRACT_BYTES``.

        Oversized files are truncated to ``MAX_CONTRACT_BYTES`` and suffixed
        with a marker so callers can tell the payload was clipped.
        """
        with open(path, "rb") as fh:
            raw = fh.read(MAX_CONTRACT_BYTES + 1)
        truncated = len(raw) > MAX_CONTRACT_BYTES
        if truncated:
            raw = raw[:MAX_CONTRACT_BYTES]
        text = raw.decode("utf-8", errors="replace")
        if truncated:
            text += "\n... [truncated: contract exceeds %d bytes]" % MAX_CONTRACT_BYTES
        return text

    def get_contract(self, path):
        """Return the contract payload (contract §3.5)."""
        with self.lock:
            node = self.get_node(path)
            if node is None:
                raise NotFoundError(path)
            ref = node["contract_ref"]
            abs_path = None
            display_path = None
            if ref:
                abs_path, display_path = self._resolve_contract_ref(ref)
            text = None
            if abs_path and os.path.isfile(abs_path):
                try:
                    text = self._read_contract_file(abs_path)
                except OSError:
                    text = None
            links = self._get_links(node["id"])
            return {
                "node": node,
                "contract_ref": ref,
                "contract_text": text,
                "contract_path": display_path,
                "links": links,
            }

    # ------------------------------------------------------------ session links

    def set_session_link(self, node_path, session_id):
        """Mount a session on exactly one node (delete-then-insert).

        A ``session_id`` is unique across the service: re-mounting the same
        session on a *different* node first removes the previous link so the
        session never points at two nodes.
        """
        node_path = normalize_path(node_path)
        with self.lock:
            node = self.get_node(node_path)
            if node is None:
                raise NotFoundError(node_path)
            now = self._now()
            # Drop any existing link for this session (on any node) first, so a
            # session is mounted on exactly one node.
            self.conn.execute(
                "DELETE FROM node_links WHERE kind = 'session' AND ref = ?",
                (session_id,),
            )
            self.conn.execute(
                """
                INSERT INTO node_links (node_id, kind, ref, created_at)
                VALUES (?, 'session', ?, ?)
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
        """Create a batch of nodes in a single transaction (contract §3.7).

        ``source`` must be ``agents`` or ``frontmatter``.  ``nodes`` carries the
        actual node dicts to materialise (the parsing of AGENTS.md / frontmatter
        belongs to P3; this store just persists what it is given).

        The whole batch is committed atomically: if any node fails validation or
        insertion, the transaction is rolled back and nothing is left behind.
        ``root_path`` (when given) scopes the batch: every node path must be
        under it.
        """
        if source not in ("agents", "frontmatter"):
            raise ValidationError("invalid import source: %s" % source)
        items = list(nodes or [])
        if not items:
            return {"imported": 0, "nodes": []}

        # --- pre-validation (nothing written yet) ---
        batch_paths = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValidationError("each imported node must be an object")
            path = item.get("path")
            if not path:
                raise ValidationError("each imported node requires a path")
            normalize_path(path)
            batch_paths.add(path)
        if root_path:
            rp = root_path.rstrip("/")
            normalize_path(rp)
            for path in batch_paths:
                if not (path == rp or path.startswith(rp + "/")):
                    raise ValidationError(
                        "node path %r is not under root_path %r" % (path, rp)
                    )

        prepared = []
        seen = set()
        for item in items:
            path = normalize_path(item["path"])
            if path in seen:
                raise ConflictError("duplicate node path %r in import" % path)
            seen.add(path)
            parent = (
                normalize_path(item.get("parent_path"))
                if item.get("parent_path")
                else None
            )
            kind = item.get("kind", "component")
            if kind not in KINDS:
                raise ValidationError("invalid kind: %s" % kind)
            if parent:
                if parent == path:
                    raise ValidationError(
                        "cycle: a node cannot be its own parent (%s)" % path
                    )
                if parent.startswith(path + "/"):
                    raise ValidationError(
                        "cycle: parent %r is a descendant of %r" % (parent, path)
                    )
                if parent not in batch_paths and self.get_node(parent) is None:
                    raise NotFoundError("parent node not found: %s" % parent)
            m = dict(item.get("meta")) if isinstance(item.get("meta"), dict) else {}
            if item.get("workspace") is not None:
                m["workspace"] = item["workspace"]
            elif workspace is not None:
                m["workspace"] = workspace
            prepared.append(
                {
                    "path": path,
                    "parent_path": parent,
                    "kind": kind,
                    "name": item.get("name") or path.split("/")[-1],
                    "desc": item.get("desc"),
                    "contract_ref": validate_contract_ref(item.get("contract_ref")),
                    "meta": json.dumps(m, ensure_ascii=False),
                }
            )

        # --- single transaction: all-or-nothing ---
        now = self._now()
        with self.lock:
            try:
                created = []
                for rec in prepared:
                    cur = self.conn.execute(
                        """
                        INSERT INTO worktree_nodes
                          (path, parent_path, kind, name, desc, contract_ref,
                           meta, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rec["path"],
                            rec["parent_path"],
                            rec["kind"],
                            rec["name"],
                            rec["desc"],
                            rec["contract_ref"],
                            rec["meta"],
                            now,
                            now,
                        ),
                    )
                    created.append(self._get_node_by_id(cur.lastrowid))
                self.conn.commit()
            except sqlite3.IntegrityError as exc:
                self.conn.rollback()
                raise ConflictError("node path exists during import") from exc
            except Exception:
                self.conn.rollback()
                raise
        return {"imported": len(created), "nodes": created}
