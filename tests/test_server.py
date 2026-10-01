"""End-to-end tests for fitlog-mcp over real HTTP (stdlib unittest + urllib)."""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp import PROTOCOL_VERSION
from mcp.transport import create_server

EXPECTED_TOOLS = {
    "log_workout",
    "get_history",
    "get_personal_records",
    "plan_workout",
    "log_protein",
    "get_nutrition_targets",
}


class FitLogServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db = os.path.join(cls._tmp.name, "test.db")
        cls.httpd, cls.store = create_server("127.0.0.1", 0, db)
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls._thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.store.close()
        cls._tmp.cleanup()

    # -- helpers ----------------------------------------------------------

    def _post(self, payload, session=None, version=PROTOCOL_VERSION,
              origin=None, raw_body=None):
        headers = {"Content-Type": "application/json"}
        if session:
            headers["Mcp-Session-Id"] = session
        if version is not None:
            headers["MCP-Protocol-Version"] = version
        if origin:
            headers["Origin"] = origin
        body = raw_body if raw_body is not None else json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base + "/mcp", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def _new_session(self):
        status, headers, body = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
            version=None,
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["result"]["protocolVersion"], PROTOCOL_VERSION)
        sid = headers.get("Mcp-Session-Id")
        self.assertTrue(sid, "initialize must issue Mcp-Session-Id")
        # notifications/initialized -> 202, no body
        status, _, _ = self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session=sid,
        )
        self.assertEqual(status, 202)
        return sid

    def _call(self, session, tool, arguments):
        status, _, body = self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
            session=session,
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertNotIn("error", data)
        return data["result"]

    # -- handshake ----------------------------------------------------------

    def test_initialize_handshake(self):
        sid = self._new_session()
        self.assertTrue(len(sid) >= 16)

    def test_health_endpoint(self):
        with urllib.request.urlopen(self.base + "/health", timeout=10) as resp:
            data = json.loads(resp.read())
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["protocol"], PROTOCOL_VERSION)

    def test_ping(self):
        sid = self._new_session()
        status, _, body = self._post(
            {"jsonrpc": "2.0", "id": 9, "method": "ping"}, session=sid
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["result"], {})

    # -- tools ----------------------------------------------------------------

    def test_tools_list(self):
        sid = self._new_session()
        status, _, body = self._post(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, session=sid
        )
        self.assertEqual(status, 200)
        tools = json.loads(body)["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, EXPECTED_TOOLS)
        by_name = {t["name"]: t for t in tools}
        # annotation spot checks
        self.assertTrue(by_name["log_workout"]["annotations"]["destructiveHint"])
        self.assertFalse(by_name["log_workout"]["annotations"]["readOnlyHint"])
        self.assertTrue(by_name["get_history"]["annotations"]["readOnlyHint"])
        self.assertTrue(by_name["get_personal_records"]["annotations"]["readOnlyHint"])
        for t in tools:
            self.assertIn("inputSchema", t)
            self.assertIn("annotations", t)

    def test_log_workout_roundtrip(self):
        sid = self._new_session()
        res = self._call(
            sid,
            "log_workout",
            {
                "exercise": "Bench Press",
                "sets": [{"reps": 8, "weight": 80}, {"reps": 6, "weight": 85}],
            },
        )
        self.assertFalse(res["isError"])
        text = res["content"][0]["text"]
        self.assertIn("Bench Press", text)
        self.assertIn("New PR", text)

        hist = self._call(sid, "get_history", {"exercise": "Bench Press"})
        self.assertIn("Bench Press", hist["content"][0]["text"])

        prs = self._call(sid, "get_personal_records", {})
        pr_text = prs["content"][0]["text"]
        self.assertIn("Bench Press", pr_text)
        self.assertIn("85", pr_text)

    def test_log_workout_validation(self):
        sid = self._new_session()
        res = self._call(sid, "log_workout", {"exercise": "Squat", "sets": []})
        self.assertTrue(res["isError"])
        res = self._call(sid, "log_workout", {"sets": [{"reps": 5, "weight": 100}]})
        self.assertTrue(res["isError"])

    def test_plan_workout(self):
        sid = self._new_session()
        res = self._call(sid, "plan_workout", {"day": "monday"})
        self.assertFalse(res["isError"])
        self.assertIn("Back", res["content"][0]["text"])
        res = self._call(sid, "plan_workout", {"day": "funday"})
        self.assertTrue(res["isError"])
        res = self._call(sid, "plan_workout", {})
        self.assertFalse(res["isError"])  # defaults to today

    def test_protein_flow(self):
        sid = self._new_session()
        res = self._call(sid, "log_protein", {"grams": 30})
        self.assertFalse(res["isError"])
        self.assertIn("30 g", res["content"][0]["text"])
        res = self._call(sid, "get_nutrition_targets", {})
        text = res["content"][0]["text"]
        self.assertIn("160-190", text)
        self.assertIn("30", text)

    def test_unknown_tool(self):
        sid = self._new_session()
        res = self._call(sid, "nope", {})
        self.assertTrue(res["isError"])

    # -- resources --------------------------------------------------------------

    def test_resources(self):
        sid = self._new_session()
        status, _, body = self._post(
            {"jsonrpc": "2.0", "id": 4, "method": "resources/list"}, session=sid
        )
        uris = {r["uri"] for r in json.loads(body)["result"]["resources"]}
        self.assertEqual(uris, {"program://current", "pr://all"})

        status, _, body = self._post(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "resources/read",
                "params": {"uri": "program://current"},
            },
            session=sid,
        )
        contents = json.loads(body)["result"]["contents"]
        program = json.loads(contents[0]["text"])
        self.assertIn("monday", program)

        status, _, body = self._post(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "resources/read",
                "params": {"uri": "bogus://x"},
            },
            session=sid,
        )
        self.assertIn("error", json.loads(body))

    # -- transport hardening ------------------------------------------------------

    def test_origin_rejection(self):
        sid = self._new_session()
        status, _, _ = self._post(
            {"jsonrpc": "2.0", "id": 7, "method": "ping"},
            session=sid,
            origin="https://evil.example",
        )
        self.assertEqual(status, 403)

    def test_origin_localhost_allowed(self):
        sid = self._new_session()
        status, _, _ = self._post(
            {"jsonrpc": "2.0", "id": 7, "method": "ping"},
            session=sid,
            origin="http://localhost:3000",
        )
        self.assertEqual(status, 200)

    def test_bad_protocol_version(self):
        sid = self._new_session()
        status, _, _ = self._post(
            {"jsonrpc": "2.0", "id": 8, "method": "ping"},
            session=sid,
            version="1999-01-01",
        )
        self.assertEqual(status, 400)

    def test_missing_session(self):
        status, _, _ = self._post({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(status, 400)

    def test_session_termination(self):
        sid = self._new_session()
        req = urllib.request.Request(
            self.base + "/mcp",
            headers={"Mcp-Session-Id": sid},
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
        # old session is dead
        status, _, _ = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}, session=sid
        )
        self.assertEqual(status, 404)
        # double delete -> 404
        req = urllib.request.Request(
            self.base + "/mcp",
            headers={"Mcp-Session-Id": sid},
            method="DELETE",
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("expected 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


if __name__ == "__main__":
    unittest.main()
