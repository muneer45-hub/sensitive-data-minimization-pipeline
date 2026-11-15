"""Local dashboard.  Run:  python -m sensitive_pipeline.web   ->  http://127.0.0.1:8000
Standard library only (http.server). Read-only JSON API + one POST endpoint to trigger a pipeline run."""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import load_config
from .pipeline import run_pipeline
from .profiler import profile_file
from .reconcile import SAMPLE_QUERIES, evaluate_labels, run_checks
from .schema import ENTITY_SPECS, PROCESSING_ORDER

STATIC_DIR = Path(__file__).resolve().parent.parent / "web"
RUN_LOCK = threading.Lock()
LABELS_PATH = Path("sample_data/labels/expected_labels.csv")


def _rows(conn, sql, params=()):
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


# ------------------------------------------------------------------ API handlers
def api_summary(cfg, conn, q):
    latest = _rows(conn, "SELECT * FROM pipeline_run ORDER BY started_at DESC, rowid DESC LIMIT 1")
    counts = {
        "silver_customers": _one(conn, "SELECT COUNT(*) FROM silver_customers"),
        "silver_orders": _one(conn, "SELECT COUNT(*) FROM silver_orders"),
        "history_rows": _one(conn, "SELECT COUNT(*) FROM silver_customers_history"),
        "quarantine": _one(conn, "SELECT COUNT(*) FROM quarantine_records"),
        "gold_customers": _one(conn, "SELECT COUNT(*) FROM gold_dim_customer"),
        "gold_orders": _one(conn, "SELECT COUNT(*) FROM gold_fact_order"),
        "bronze_files": _one(conn, "SELECT COUNT(*) FROM bronze_file_manifest WHERE status='SUCCESS'"),
        "runs": _one(conn, "SELECT COUNT(*) FROM pipeline_run"),
    }
    return {
        "ready": True,
        "latest_run": latest[0] if latest else None,
        "counts": counts,
        "reject_reasons": _rows(conn, "SELECT reject_reason AS label, COUNT(*) AS value FROM quarantine_records "
                                      "GROUP BY reject_reason ORDER BY value DESC"),
        "orders_by_status": _rows(conn, "SELECT status AS label, COUNT(*) AS value FROM gold_fact_order "
                                        "GROUP BY status ORDER BY value DESC"),
        "revenue_by_country": _rows(conn, "SELECT country_code AS label, total_amount AS value FROM "
                                          "gold_sales_by_country WHERE currency='USD' ORDER BY value DESC"),
        "protection": _rows(conn, "SELECT entity, field, action, rows_affected FROM audit_log WHERE audit_id IN "
                                  "(SELECT MAX(audit_id) FROM audit_log WHERE layer='silver' AND field IS NOT NULL "
                                  "GROUP BY entity, field) ORDER BY entity, field"),
    }


def api_runs(cfg, conn, q):
    runs = _rows(conn, "SELECT * FROM pipeline_run ORDER BY started_at DESC, rowid DESC LIMIT 25")
    for r in runs:
        r["entities"] = _rows(conn, "SELECT * FROM entity_run_metrics WHERE run_id=? ORDER BY entity", (r["run_id"],))
    return {"ready": True, "runs": runs}


def api_quarantine(cfg, conn, q):
    where, params = [], []
    if q.get("entity"):
        where.append("entity=?")
        params.append(q["entity"])
    if q.get("reason"):
        where.append("reject_reason LIKE ?")
        params.append(f"%{q['reason']}%")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = _one(conn, f"SELECT COUNT(*) FROM quarantine_records {clause}", params)
    rows = _rows(conn, f"SELECT entity, source_file, source_line_number, record_key, reject_reason, "
                       f"raw_record_redacted, run_id FROM quarantine_records {clause} "
                       f"ORDER BY entity, source_line_number LIMIT 200", params)
    return {"ready": True, "total": total, "rows": rows}


def api_checks(cfg, conn, q):
    out = {"ready": True, "checks": run_checks(conn), "labels": None}
    if LABELS_PATH.exists():
        out["labels"] = evaluate_labels(conn, LABELS_PATH)
    return out


def api_profile(cfg, conn, q):
    profiles = []
    for e in PROCESSING_ORDER:
        path = cfg.input_dir / ENTITY_SPECS[e]["file"]
        if path.exists():
            profiles.append(profile_file(path, e, cfg.policies.get(e, {})))
    return {"ready": True, "profiles": profiles}


def api_privacy(cfg, conn, q):
    """Same customer at three levels: raw source file -> silver (protected) -> gold (minimized). Synthetic demo data."""
    path = cfg.input_dir / "customers.csv"
    out, seen = [], set()
    if path.exists():
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for raw in csv.DictReader(fh):
                cid = raw.get("customer_id")
                if cid in seen:
                    continue
                s = conn.execute("SELECT full_name_p,email_p,phone_p,ssn_p,dob_p FROM silver_customers "
                                 "WHERE customer_id=?", (cid,)).fetchone()
                g = conn.execute("SELECT email_token,birth_year,country_code,signup_date FROM gold_dim_customer "
                                 "WHERE customer_id=?", (cid,)).fetchone()
                if not s or not g:
                    continue
                seen.add(cid)
                out.append({"customer_id": cid,
                            "raw": {k: raw.get(k) for k in ("full_name", "email", "phone", "ssn", "dob")},
                            "silver": dict(zip(("full_name", "email", "phone", "ssn", "dob"), s)),
                            "gold": dict(zip(("email_token", "birth_year", "country_code", "signup_date"), g))})
                if len(out) >= 6:
                    break
    return {"ready": True, "policies": cfg.policies.get("customers", {}), "rows": out}


def api_audit(cfg, conn, q):
    return {"ready": True, "rows": _rows(
        conn, "SELECT audit_id, run_id, event_ts, layer, entity, field, action, rows_affected, details "
              "FROM audit_log ORDER BY audit_id DESC LIMIT 150")}


def api_analytics(cfg, conn, q):
    results = []
    for title, sql in SAMPLE_QUERIES.items():
        cur = conn.execute(sql)
        results.append({"title": title, "sql": sql, "columns": [d[0] for d in cur.description],
                        "rows": cur.fetchall()})
    return {"ready": True, "queries": results,
            "gold_customers": _rows(conn, "SELECT * FROM gold_dim_customer ORDER BY customer_id LIMIT 12"),
            "gold_orders": _rows(conn, "SELECT * FROM gold_fact_order ORDER BY order_id LIMIT 12")}


ROUTES = {"/api/summary": api_summary, "/api/runs": api_runs, "/api/quarantine": api_quarantine,
          "/api/checks": api_checks, "/api/profile": api_profile, "/api/privacy": api_privacy,
          "/api/audit": api_audit, "/api/analytics": api_analytics}


# ------------------------------------------------------------------ HTTP plumbing
def make_handler(cfg):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the terminal quiet
            pass

        def _send(self, body: bytes, ctype: str, code=200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(json.dumps(obj, default=str).encode("utf-8"), "application/json; charset=utf-8", code)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                self._send((STATIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif u.path in ROUTES:
                if not cfg.db_path.exists():
                    return self._json({"ready": False})
                conn = sqlite3.connect(str(cfg.db_path))
                try:
                    q = {k: v[0] for k, v in parse_qs(u.query).items()}
                    self._json(ROUTES[u.path](cfg, conn, q))
                except sqlite3.Error as exc:
                    self._json({"ready": False, "error": str(exc)}, 500)
                finally:
                    conn.close()
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if urlparse(self.path).path != "/api/run":
                return self._json({"error": "not found"}, 404)
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not RUN_LOCK.acquire(blocking=False):
                return self._json({"error": "a run is already in progress"}, 409)
            try:
                self._json(run_pipeline(cfg, force=bool(body.get("force"))))
            finally:
                RUN_LOCK.release()

    return Handler


def main(argv=None):
    from .cli import _setup_logging
    p = argparse.ArgumentParser(prog="sensitive_pipeline.web")
    p.add_argument("--config", default="config/pipeline_config.json")
    p.add_argument("--input-dir")
    p.add_argument("--output-dir")
    p.add_argument("--port", type=int, default=8000)
    a = p.parse_args(argv)
    cfg = load_config(a.config, a.input_dir, a.output_dir)
    _setup_logging(cfg.output_dir)
    server = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(cfg))
    print(f"Dashboard running at http://127.0.0.1:{a.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()