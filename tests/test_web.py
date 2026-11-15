import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from sensitive_pipeline.web import make_handler
from tests.helpers import PipelineTestCase, cust, order


class WebApiTests(PipelineTestCase):
    def setUp(self):
        super().setUp()
        self.put([cust(1), cust(2)], [order(1, 1)])
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cfg))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return json.loads(r.read())

    def post(self, path, payload):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def test_not_ready_before_first_run_then_run_from_ui(self):
        self.assertFalse(self.get("/api/summary")["ready"])
        result = self.post("/api/run", {"force": False})
        self.assertEqual(result["status"], "SUCCESS")
        s = self.get("/api/summary")
        self.assertEqual(s["counts"]["silver_customers"], 2)
        self.assertEqual(s["latest_run"]["status"], "SUCCESS")

    def test_endpoints_after_run(self):
        self.post("/api/run", {})
        for ep in ("runs", "quarantine", "checks", "profile", "privacy", "audit", "analytics"):
            self.assertTrue(self.get(f"/api/{ep}")["ready"], ep)
        priv = self.get("/api/privacy")["rows"][0]
        self.assertTrue(priv["silver"]["email"].startswith("tok_"))
        self.assertNotIn("ssn", priv["gold"])

    def test_dashboard_html_served(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as r:
            self.assertIn(b"Sensitive Data", r.read())


if __name__ == "__main__":
    unittest.main()