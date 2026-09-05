"""Self-contained smoke tests for the dsh-workbench P0 worktree service.

Run from the `worktree-server/` directory:
    /opt/AstrBot/venv/bin/python3 tests/test_worktree.py
or
    /opt/AstrBot/venv/bin/python3 -m unittest tests.test_worktree
"""

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)  # worktree-server/
sys.path.insert(0, REPO)

from worktree import (  # noqa: E402
    ConflictError,
    NotFoundError,
    ValidationError,
    WorktreeStore,
    normalize_path,
)

import server  # noqa: E402

TOKEN = "test-token-123"


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestStore(unittest.TestCase):
    """Direct store tests against a fresh temp sqlite file (deterministic)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="worktree-store-")
        self.db = os.path.join(self.tmp, "worktree.db")
        self.store = WorktreeStore(self.db, contract_base=self.tmp)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self):
        self.store.create_node(
            "deepmemory", kind="root", name="deepmemory", workspace="deepseek-harness"
        )
        self.store.create_node(
            "deepmemory/主体",
            parent_path="deepmemory",
            kind="component",
            name="主体",
            workspace="deepseek-harness",
        )
        self.store.create_node(
            "deepmemory/memory-server",
            parent_path="deepmemory/主体",
            kind="service",
            name="memory-server",
            desc="语义长期记忆后端服务",
            contract_ref="docs/x.md",
            meta={
                "port": 6230,
                "path": "data/memory.db",
                "interfaces": ["export-archive", "get_sources"],
                "status": "active",
                "workspace": "deepseek-harness",
            },
            workspace="deepseek-harness",
        )

    def test_create_and_serialization_shape(self):
        # A node's parent must exist (validated on create), so seed the chain.
        self.store.create_node("deepmemory", kind="root", name="deepmemory")
        self.store.create_node(
            "deepmemory/主体", parent_path="deepmemory", kind="component", name="主体"
        )
        node = self.store.create_node(
            "deepmemory/memory-server",
            parent_path="deepmemory/主体",
            kind="service",
            name="memory-server",
            desc="服务",
            contract_ref="docs/x.md",
            meta={"port": 6230, "status": "active", "workspace": "deepseek-harness"},
            workspace="deepseek-harness",
        )
        self.assertEqual(node["path"], "deepmemory/memory-server")
        self.assertEqual(node["kind"], "service")
        self.assertEqual(node["workspace"], "deepseek-harness")
        # meta must be a parsed object, not a string
        self.assertEqual(node["meta"]["port"], 6230)
        self.assertIsInstance(node["meta"], dict)
        # workspace is the top-level redundancy of meta.workspace
        self.assertEqual(node["workspace"], node["meta"]["workspace"])
        # timestamps are float
        self.assertIsInstance(node["created_at"], float)
        self.assertIsInstance(node["updated_at"], float)

    def test_create_conflict_409(self):
        self.store.create_node("a", kind="root")
        with self.assertRaises(ConflictError):
            self.store.create_node("a", kind="root")

    def test_get_node_missing(self):
        self.assertIsNone(self.store.get_node("nope"))

    def test_list_nodes_filter_workspace(self):
        self._seed()
        self.store.create_node("other", kind="root", workspace="w2")
        all_nodes = self.store.list_nodes()
        self.assertEqual({n["path"] for n in all_nodes}, {"deepmemory", "deepmemory/主体", "deepmemory/memory-server", "other"})
        only_hs = self.store.list_nodes("deepseek-harness")
        self.assertEqual({n["path"] for n in only_hs}, {"deepmemory", "deepmemory/主体", "deepmemory/memory-server"})

    def test_update_node(self):
        self._seed()
        updated = self.store.update_node(
            "deepmemory/memory-server",
            {"desc": "new desc", "meta": {"status": "inactive"}},
        )
        self.assertEqual(updated["desc"], "new desc")
        self.assertEqual(updated["meta"]["status"], "inactive")
        # existing meta keys preserved
        self.assertEqual(updated["meta"]["port"], 6230)
        # touch updated_at
        self.assertGreaterEqual(updated["updated_at"], updated["created_at"])

    def test_get_ancestors_order(self):
        self._seed()
        anc = self.store.get_ancestors("deepmemory/memory-server")
        self.assertEqual(
            [n["path"] for n in anc],
            ["deepmemory", "deepmemory/主体", "deepmemory/memory-server"],
        )

    def test_get_ancestors_missing(self):
        with self.assertRaises(NotFoundError):
            self.store.get_ancestors("nope")

    def test_get_tree_with_root(self):
        self._seed()
        tree = self.store.get_tree("deepmemory", depth=2)
        self.assertEqual(tree["root"]["path"], "deepmemory")
        self.assertEqual(tree["root"].get("children"), None)
        # two levels below root
        children = tree["children"]
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["path"], "deepmemory/主体")
        grand = children[0]["children"]
        self.assertEqual(len(grand), 1)
        self.assertEqual(grand[0]["path"], "deepmemory/memory-server")

    def test_get_tree_forest(self):
        self._seed()
        self.store.create_node("other", kind="root")
        tree = self.store.get_tree(depth=1)
        self.assertIsNone(tree["root"])
        roots = [c["path"] for c in tree["children"]]
        self.assertEqual(set(roots), {"deepmemory", "other"})

    def test_get_tree_missing_root(self):
        with self.assertRaises(NotFoundError):
            self.store.get_tree("nope", depth=1)

    def test_delete_node(self):
        self._seed()
        self.store.set_session_link("deepmemory/memory-server", "sess-1")
        res = self.store.delete_node("deepmemory/memory-server")
        self.assertEqual(res, {"ok": True, "deleted": 1, "links": 1})
        self.assertIsNone(self.store.get_node("deepmemory/memory-server"))

    def test_session_link_roundtrip(self):
        self._seed()
        self.store.set_session_link("deepmemory/memory-server", "sess-1")
        s = self.store.get_session("sess-1")
        self.assertEqual(s["node_path"], "deepmemory/memory-server")
        self.assertEqual(s["node"]["kind"], "service")
        self.assertEqual(
            [n["path"] for n in s["ancestors"]],
            ["deepmemory", "deepmemory/主体", "deepmemory/memory-server"],
        )
        # idempotent upsert
        self.store.set_session_link("deepmemory/memory-server", "sess-1")

    def test_session_not_mounted(self):
        self._seed()
        s = self.store.get_session("does-not-exist")
        self.assertEqual(s, {"node_path": None, "node": None, "ancestors": []})

    def test_session_link_missing_node(self):
        with self.assertRaises(NotFoundError):
            self.store.set_session_link("nope", "sess-1")

    def test_remove_session_link(self):
        self._seed()
        self.store.set_session_link("deepmemory/memory-server", "sess-1")
        res = self.store.remove_session_link("sess-1")
        self.assertEqual(res["ok"], True)
        self.assertEqual(self.store.get_session("sess-1"), {"node_path": None, "node": None, "ancestors": []})

    def test_contract_reads_file(self):
        self._seed()
        docs = os.path.join(self.tmp, "docs")
        os.makedirs(docs, exist_ok=True)
        with open(os.path.join(docs, "x.md"), "w", encoding="utf-8") as fh:
            fh.write("# contract body")
        c = self.store.get_contract("deepmemory/memory-server")
        self.assertEqual(c["contract_ref"], "docs/x.md")
        self.assertEqual(c["contract_text"], "# contract body")
        self.assertTrue(c["contract_path"].endswith("docs/x.md"))

    def test_contract_no_ref(self):
        node = self.store.create_node("plain", kind="component", name="plain")
        c = self.store.get_contract("plain")
        self.assertIsNone(c["contract_ref"])
        self.assertIsNone(c["contract_text"])
        self.assertIsNone(c["contract_path"])

    def test_import_nodes(self):
        nodes = [
            {"path": "app", "kind": "root", "name": "app", "workspace": "w"},
            {"path": "app/api", "parent_path": "app", "kind": "service", "name": "api"},
        ]
        res = self.store.import_nodes(source="agents", nodes=nodes, workspace="w")
        self.assertEqual(res["imported"], 2)
        self.assertEqual(len(res["nodes"]), 2)
        self.assertEqual(res["nodes"][1]["workspace"], "w")

    def test_import_bad_source(self):
        with self.assertRaises(ValidationError):
            self.store.import_nodes(source="nope", nodes=[])

    def test_normalize_path_rejects_bad(self):
        bad = ["", "a/../b", "a/./b", "/etc/passwd", "a\x00b", "a/" + "b" * 600]
        for p in bad:
            with self.assertRaises(ValidationError):
                normalize_path(p)
        self.assertEqual(
            normalize_path("deepmemory/memory-server"), "deepmemory/memory-server"
        )

    def test_create_requires_existing_parent(self):
        with self.assertRaises(NotFoundError):
            self.store.create_node("a/b", parent_path="a", kind="component", name="b")

    def test_create_rejects_cycle(self):
        self.store.create_node("a", kind="root", name="a")
        self.store.create_node("a/b", parent_path="a", kind="component", name="b")
        # self-parenting
        with self.assertRaises(ValidationError):
            self.store.create_node(
                "a/b/c", parent_path="a/b/c", kind="component", name="c"
            )

    def test_update_rejects_cycle(self):
        self.store.create_node("a", kind="root", name="a")
        self.store.create_node("a/b", parent_path="a", kind="component", name="b")
        self.store.create_node("a/b/c", parent_path="a/b", kind="component", name="c")
        # move `a` under its own descendant -> cycle
        with self.assertRaises(ValidationError):
            self.store.update_node("a", {"parent_path": "a/b/c"})
        # self-parent
        with self.assertRaises(ValidationError):
            self.store.update_node("a", {"parent_path": "a"})

    def test_delete_cascades(self):
        self.store.create_node("a", kind="root", name="a")
        self.store.create_node("a/b", parent_path="a", kind="component", name="b")
        self.store.create_node("a/b/c", parent_path="a/b", kind="component", name="c")
        self.store.set_session_link("a/b/c", "sess-1")
        res = self.store.delete_node("a")
        self.assertEqual(res["deleted"], 3)
        self.assertEqual(res["links"], 1)
        for p in ("a", "a/b", "a/b/c"):
            self.assertIsNone(self.store.get_node(p))

    def test_session_link_unique_mount(self):
        self._seed()
        self.store.create_node(
            "deepmemory/other", parent_path="deepmemory", kind="component", name="other"
        )
        self.store.set_session_link("deepmemory/主体", "sess-unique")
        self.store.set_session_link("deepmemory/other", "sess-unique")
        s = self.store.get_session("sess-unique")
        self.assertEqual(s["node_path"], "deepmemory/other")
        links = self.store.get_node_links("deepmemory/主体")
        self.assertFalse(
            any(
                l["kind"] == "session" and l["ref"] == "sess-unique" for l in links
            )
        )

    def test_contract_rejects_traversal_on_write(self):
        # A traversal contract_ref is rejected up front (create/update).
        with self.assertRaises(ValidationError):
            self.store.create_node(
                "esc", kind="component", name="esc", contract_ref="../../etc/passwd"
            )
        self.store.create_node("upd", kind="component", name="upd")
        with self.assertRaises(ValidationError):
            self.store.update_node("upd", {"contract_ref": "/etc/passwd"})

    def test_contract_read_hardened_for_legacy_data(self):
        # Simulate legacy/corrupt data inserted directly (bypassing validation)
        # so get_contract must still refuse to read outside the base.
        self.store.conn.execute(
            "INSERT INTO worktree_nodes (path, parent_path, kind, name, desc, "
            "contract_ref, meta, created_at, updated_at) "
            "VALUES ('legacy', NULL, 'component', 'legacy', NULL, "
            "'../../../etc/passwd', NULL, 1, 1)"
        )
        self.store.conn.commit()
        c = self.store.get_contract("legacy")
        self.assertIsNone(c["contract_text"])
        self.assertIsNone(c["contract_path"])

    def test_get_tree_depth_limit(self):
        self._seed()
        with self.assertRaises(ValidationError):
            self.store.get_tree(depth=1000)

    def test_get_tree_cycle_detected(self):
        self.store.create_node("a", kind="root", name="a")
        self.store.create_node("a/b", parent_path="a", kind="component", name="b")
        # Corrupt the hierarchy into a cycle (a -> a/b -> a).
        self.store.conn.execute(
            "UPDATE worktree_nodes SET parent_path = 'a/b' WHERE path = 'a'"
        )
        self.store.conn.commit()
        with self.assertRaises(ConflictError):
            self.store.get_tree("a", depth=5)

    def test_import_transaction_rollback(self):
        self.store.create_node("dup", kind="root", name="dup")
        with self.assertRaises(ConflictError):
            self.store.import_nodes(
                source="agents",
                nodes=[
                    {"path": "new1", "kind": "root", "name": "new1"},
                    {"path": "dup", "kind": "root", "name": "dup"},
                ],
            )
        # Nothing from the failed batch may remain (single transaction).
        self.assertIsNone(self.store.get_node("new1"))

    def test_import_root_path_scopes_batch(self):
        with self.assertRaises(ValidationError):
            self.store.import_nodes(
                source="agents",
                root_path="scope",
                nodes=[{"path": "outside", "kind": "root", "name": "x"}],
            )


class TestHTTPServer(unittest.TestCase):
    """HTTP-level smoke test against a thread-started server using api-token."""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="worktree-http-")
        cls.port = _free_port()
        cls.base = "http://localhost:%d" % cls.port
        cls.data_dir = os.path.join(cls.tmp, "data")
        os.makedirs(cls.data_dir, exist_ok=True)
        token_file = os.path.join(cls.data_dir, "api-token")
        with open(token_file, "w", encoding="utf-8") as fh:
            fh.write(TOKEN)
        # contract base: create docs/x.md
        docs = os.path.join(cls.tmp, "docs")
        os.makedirs(docs, exist_ok=True)
        with open(os.path.join(docs, "x.md"), "w", encoding="utf-8") as fh:
            fh.write("# contract from file")

        cls.httpd = server.create_server(
            cls.data_dir, cls.port, token_file=token_file, contract_base=cls.tmp
        )
        cls.thread = threading.Thread(
            target=cls.httpd.serve_forever, daemon=True
        )
        cls.thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _req(self, method, path, body=None, token=TOKEN, origin=None):
        url = self.base + path
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if origin is not None:
            headers["Origin"] = origin
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_health(self):
        status, body = self._req("GET", "/v1/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["version"], "0.1.0")
        self.assertEqual(body["db"], "worktree.db")

    def test_auth_required(self):
        # no token -> 401 (memory-server same-stack pattern; api-contract §5)
        status, body = self._req("GET", "/v1/health", token=None)
        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_auth_origin_rejected(self):
        status, body = self._req("GET", "/v1/health", origin="http://evil.example")
        self.assertEqual(status, 403)

    def _create(self, path, parent_path=None, kind="component", name=None, **kw):
        body = {"path": path, "kind": kind, "name": name or path.split("/")[-1]}
        if parent_path is not None:
            body["parent_path"] = parent_path
        body.update(kw)
        status, resp = self._req("POST", "/v1/worktree/nodes", body)
        self.assertEqual(status, 200, "create %s -> %r" % (path, resp))
        return resp["node"]

    def test_crud_http(self):
        self._create("crud", kind="root", name="crud", workspace="deepseek-harness")
        self._create("crud/主体", parent_path="crud", name="主体")
        node = self._create(
            "crud/memory-server",
            parent_path="crud/主体",
            kind="service",
            name="memory-server",
            meta={"port": 6230, "workspace": "deepseek-harness"},
        )
        self.assertEqual(node["meta"]["port"], 6230)

        # duplicate -> 409
        status, body = self._req(
            "POST", "/v1/worktree/nodes", {"path": "crud", "kind": "root"}
        )
        self.assertEqual(status, 409)

        # list with workspace filter
        status, body = self._req(
            "GET", "/v1/worktree/nodes?workspace=deepseek-harness"
        )
        self.assertEqual(status, 200)
        self.assertIn("crud", {n["path"] for n in body["nodes"]})

        # get single node with links
        status, body = self._req("GET", "/v1/worktree/nodes/crud/memory-server")
        self.assertEqual(status, 200)
        self.assertEqual(body["node"]["kind"], "service")
        self.assertIn("links", body)

    def test_encoded_slash_path(self):
        # contract §3.2 note: path segments may be URL-encoded with %2F
        self._create("enc/root", kind="root", name="enc")
        self._create("enc/root/svc", parent_path="enc/root", kind="service", name="svc")
        status, body = self._req("GET", "/v1/worktree/nodes/enc%2Froot%2Fsvc")
        self.assertEqual(status, 200)
        self.assertEqual(body["node"]["path"], "enc/root/svc")

    def test_tree_http(self):
        tree = self._seed_http("tree")
        status, body = self._req("GET", "/v1/worktree/tree?root=%s&depth=2" % tree["root"])
        self.assertEqual(status, 200)
        self.assertEqual(body["root"]["path"], tree["root"])
        child = body["children"][0]
        self.assertEqual(child["path"], tree["sub"])
        self.assertEqual(child["children"][0]["path"], tree["svc"])

    def test_ancestors_http(self):
        tree = self._seed_http("anc")
        status, body = self._req("GET", "/v1/worktree/ancestors/%s" % tree["svc"])
        self.assertEqual(status, 200)
        self.assertEqual(
            [n["path"] for n in body["ancestors"]],
            [tree["root"], tree["sub"], tree["svc"]],
        )

    def test_contract_http(self):
        self._create("app", kind="component", name="app", contract_ref="docs/x.md")
        status, body = self._req("GET", "/v1/worktree/contract/app")
        self.assertEqual(status, 200)
        self.assertEqual(body["contract_ref"], "docs/x.md")
        self.assertEqual(body["contract_text"], "# contract from file")

    def test_session_link_http(self):
        tree = self._seed_http("sess")
        status, body = self._req(
            "POST",
            "/v1/worktree/session-link",
            {"node_path": tree["svc"], "session_id": "sess-a"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["node_path"], tree["svc"])
        self.assertEqual(body["session_id"], "sess-a")

        status, body = self._req("GET", "/v1/worktree/session/sess-a")
        self.assertEqual(status, 200)
        self.assertEqual(body["node_path"], tree["svc"])
        self.assertEqual(
            [n["path"] for n in body["ancestors"]],
            [tree["root"], tree["sub"], tree["svc"]],
        )

        # unlinked session -> nulls
        status, body = self._req("GET", "/v1/worktree/session/not-linked")
        self.assertEqual(status, 200)
        self.assertEqual(body["node_path"], None)

        # delete
        status, body = self._req(
            "DELETE", "/v1/worktree/session-link", {"session_id": "sess-a"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        status, body = self._req("GET", "/v1/worktree/session/sess-a")
        self.assertEqual(status, 200)
        self.assertEqual(body["node_path"], None)

    def test_import_http(self):
        status, body = self._req(
            "POST",
            "/v1/worktree/import",
            {
                "source": "agents",
                "root_path": "imp",
                "workspace": "deepseek-harness",
                "nodes": [
                    {"path": "imp", "kind": "root", "name": "imp"},
                    {"path": "imp/api", "parent_path": "imp", "kind": "service", "name": "api"},
                ],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["imported"], 2)

    def test_delete_http(self):
        self._create("todelete", kind="root", name="todelete")
        status, body = self._req("DELETE", "/v1/worktree/nodes/todelete")
        self.assertEqual(status, 200)
        self.assertEqual(body["deleted"], 1)
        status, body = self._req("GET", "/v1/worktree/nodes/todelete")
        self.assertEqual(status, 404)

    def test_missing_node_404(self):
        status, body = self._req("GET", "/v1/worktree/nodes/does/not/exist")
        self.assertEqual(status, 404)

    def test_body_must_be_object(self):
        status, body = self._req("POST", "/v1/worktree/nodes", [1, 2, 3])
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_body_too_large_400(self):
        big = {"path": "x", "pad": "a" * (server.MAX_BODY_BYTES + 1)}
        status, body = self._req("POST", "/v1/worktree/nodes", big)
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_contract_traversal_http(self):
        # An unsafe contract_ref is rejected at create time (400).
        status, body = self._req(
            "POST",
            "/v1/worktree/nodes",
            {"path": "trav", "kind": "component", "name": "trav",
             "contract_ref": "../../etc/passwd"},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_create_missing_parent_404(self):
        status, body = self._req(
            "POST",
            "/v1/worktree/nodes",
            {"path": "orphan/x", "kind": "component", "parent_path": "orphan"},
        )
        self.assertEqual(status, 404)

    def _seed_http(self, prefix):
        root = prefix
        sub = "%s/sub" % prefix
        svc = "%s/sub/service" % prefix
        self._create(root, kind="root", name=prefix)
        self._create(sub, parent_path=root, name="sub")
        self._create(svc, parent_path=sub, kind="service", name="service")
        return {"root": root, "sub": sub, "svc": svc}


if __name__ == "__main__":
    unittest.main(verbosity=2)
