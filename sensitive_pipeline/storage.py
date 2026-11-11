"""SQLite warehouse: DDL and small data-access helpers (all SQL lives here)."""
from __future__ import annotations

import csv
import json
import os
import sqlite3
from pathlib import Path

DDL = """
-- ---------- control plane ----------
CREATE TABLE IF NOT EXISTS pipeline_run (
    run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL,
    failure_reason TEXT, input_rows INTEGER, loaded_rows INTEGER, rejected_rows INTEGER,
    stale_ignored_rows INTEGER, duration_s REAL);
CREATE TABLE IF NOT EXISTS entity_run_metrics (
    run_id TEXT NOT NULL, entity TEXT NOT NULL, source_file TEXT, status TEXT NOT NULL,
    input_rows INTEGER, inserted INTEGER, updated INTEGER, unchanged INTEGER,
    stale_ignored INTEGER, rejected INTEGER, failure_reason TEXT, duration_s REAL,
    PRIMARY KEY (run_id, entity));
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, event_ts TEXT NOT NULL,
    layer TEXT, entity TEXT, field TEXT, action TEXT, rows_affected INTEGER, details TEXT);

-- ---------- bronze (immutable landing; files kept on disk, manifest in DB) ----------
CREATE TABLE IF NOT EXISTS bronze_file_manifest (
    run_id TEXT NOT NULL, entity TEXT NOT NULL, source_file TEXT NOT NULL, sha256 TEXT NOT NULL,
    row_count INTEGER, landed_path TEXT, status TEXT NOT NULL,
    PRIMARY KEY (run_id, entity));

-- ---------- silver (validated + protected) ----------
CREATE TABLE IF NOT EXISTS silver_customers (
    customer_id TEXT PRIMARY KEY, full_name_p TEXT, email_p TEXT, phone_p TEXT, ssn_p TEXT, dob_p TEXT,
    country_code TEXT NOT NULL, signup_ts TEXT NOT NULL, updated_at TEXT NOT NULL, row_hash TEXT NOT NULL,
    first_run_id TEXT NOT NULL, last_run_id TEXT NOT NULL, source_file TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS silver_customers_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id TEXT NOT NULL,
    full_name_p TEXT, email_p TEXT, phone_p TEXT, ssn_p TEXT, dob_p TEXT,
    country_code TEXT, signup_ts TEXT, updated_at TEXT, row_hash TEXT,
    superseded_by_run_id TEXT NOT NULL, superseded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS silver_orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES silver_customers(customer_id),
    order_ts TEXT NOT NULL, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL,
    row_hash TEXT NOT NULL, first_run_id TEXT NOT NULL, source_file TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quarantine_records (
    quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, entity TEXT NOT NULL,
    source_file TEXT NOT NULL, source_sha256 TEXT NOT NULL, source_line_number INTEGER NOT NULL,
    record_key TEXT, reject_reason TEXT NOT NULL, raw_record_redacted TEXT, quarantined_at TEXT NOT NULL,
    UNIQUE (entity, source_file, source_sha256, source_line_number));

-- ---------- gold (minimized analytical layer) ----------
CREATE TABLE IF NOT EXISTS gold_dim_customer (
    customer_id TEXT PRIMARY KEY, email_token TEXT, birth_year INTEGER, country_code TEXT,
    signup_date TEXT, last_updated_at TEXT);
CREATE TABLE IF NOT EXISTS gold_fact_order (
    order_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, order_date TEXT NOT NULL, order_ts TEXT,
    amount_cents INTEGER NOT NULL, currency TEXT, status TEXT);
CREATE VIEW IF NOT EXISTS gold_daily_sales AS
    SELECT order_date, currency, COUNT(*) AS order_count, SUM(amount_cents) / 100.0 AS total_amount
    FROM gold_fact_order WHERE status <> 'CANCELLED' GROUP BY order_date, currency;
CREATE VIEW IF NOT EXISTS gold_sales_by_country AS
    SELECT c.country_code, o.currency, COUNT(*) AS order_count, SUM(o.amount_cents) / 100.0 AS total_amount
    FROM gold_fact_order o JOIN gold_dim_customer c ON c.customer_id = o.customer_id
    WHERE o.status <> 'CANCELLED' GROUP BY c.country_code, o.currency;
"""


def connect(db_path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)  # we manage transactions explicitly
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn):
    conn.executescript(DDL)


def begin(conn):
    conn.execute("BEGIN")


def commit(conn):
    conn.execute("COMMIT")


def rollback(conn):
    if conn.in_transaction:
        conn.execute("ROLLBACK")


# ---------- control plane ----------
def start_run(conn, run_id, started_at):
    conn.execute("INSERT OR REPLACE INTO pipeline_run (run_id, started_at, status) VALUES (?,?, 'RUNNING')",
                 (run_id, started_at))


def finish_run(conn, run_id, ended_at, status, failure_reason, totals, duration_s):
    conn.execute(
        "UPDATE pipeline_run SET ended_at=?, status=?, failure_reason=?, input_rows=?, loaded_rows=?, "
        "rejected_rows=?, stale_ignored_rows=?, duration_s=? WHERE run_id=?",
        (ended_at, status, failure_reason, totals["input_rows"], totals["loaded_rows"],
         totals["rejected_rows"], totals["stale_ignored_rows"], duration_s, run_id))


def write_entity_metrics(conn, m):
    conn.execute(
        "INSERT OR REPLACE INTO entity_run_metrics VALUES (:run_id,:entity,:source_file,:status,:input_rows,"
        ":inserted,:updated,:unchanged,:stale_ignored,:rejected,:failure_reason,:duration_s)", m)


def write_manifest(conn, run_id, entity, source_file, sha256, row_count, landed_path, status):
    conn.execute("INSERT OR REPLACE INTO bronze_file_manifest VALUES (?,?,?,?,?,?,?)",
                 (run_id, entity, source_file, sha256, row_count, landed_path, status))


def already_processed(conn, entity, sha256) -> bool:
    row = conn.execute("SELECT 1 FROM bronze_file_manifest WHERE entity=? AND sha256=? AND status='SUCCESS' LIMIT 1",
                       (entity, sha256)).fetchone()
    return row is not None


def audit(conn, run_id, ts, layer, entity, field, action, rows_affected, details=None):
    conn.execute("INSERT INTO audit_log (run_id,event_ts,layer,entity,field,action,rows_affected,details) "
                 "VALUES (?,?,?,?,?,?,?,?)",
                 (run_id, ts, layer, entity, field, action, rows_affected,
                  json.dumps(details, sort_keys=True) if details is not None else None))


def write_quarantine(conn, run_id, entity, source_file, sha256, line_no, key, reason, raw_redacted, ts):
    conn.execute(
        "INSERT OR REPLACE INTO quarantine_records (run_id,entity,source_file,source_sha256,source_line_number,"
        "record_key,reject_reason,raw_record_redacted,quarantined_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, entity, source_file, sha256, line_no, key, reason,
         json.dumps(raw_redacted, sort_keys=True), ts))


# ---------- silver customers ----------
def get_customer_state(conn, customer_id):
    return conn.execute("SELECT updated_at, row_hash FROM silver_customers WHERE customer_id=?",
                        (customer_id,)).fetchone()


def insert_customer(conn, rec: dict):
    conn.execute(
        "INSERT INTO silver_customers (customer_id,full_name_p,email_p,phone_p,ssn_p,dob_p,country_code,signup_ts,"
        "updated_at,row_hash,first_run_id,last_run_id,source_file) VALUES (:customer_id,:full_name_p,:email_p,"
        ":phone_p,:ssn_p,:dob_p,:country_code,:signup_ts,:updated_at,:row_hash,:run_id,:run_id,:source_file)", rec)


def archive_customer(conn, customer_id, run_id, ts):
    conn.execute(
        "INSERT INTO silver_customers_history (customer_id,full_name_p,email_p,phone_p,ssn_p,dob_p,country_code,"
        "signup_ts,updated_at,row_hash,superseded_by_run_id,superseded_at) "
        "SELECT customer_id,full_name_p,email_p,phone_p,ssn_p,dob_p,country_code,signup_ts,updated_at,row_hash,?,? "
        "FROM silver_customers WHERE customer_id=?", (run_id, ts, customer_id))


def update_customer(conn, rec: dict):
    conn.execute(
        "UPDATE silver_customers SET full_name_p=:full_name_p,email_p=:email_p,phone_p=:phone_p,ssn_p=:ssn_p,"
        "dob_p=:dob_p,country_code=:country_code,signup_ts=:signup_ts,updated_at=:updated_at,row_hash=:row_hash,"
        "last_run_id=:run_id,source_file=:source_file WHERE customer_id=:customer_id", rec)


# ---------- silver orders ----------
def get_order_hash(conn, order_id):
    row = conn.execute("SELECT row_hash FROM silver_orders WHERE order_id=?", (order_id,)).fetchone()
    return row[0] if row else None


def insert_order(conn, rec: dict):
    conn.execute(
        "INSERT INTO silver_orders (order_id,customer_id,order_ts,amount_cents,currency,status,row_hash,"
        "first_run_id,source_file) VALUES (:order_id,:customer_id,:order_ts,:amount_cents,:currency,:status,"
        ":row_hash,:run_id,:source_file)", rec)


def customer_id_set(conn) -> set:
    return {r[0] for r in conn.execute("SELECT customer_id FROM silver_customers")}


# ---------- gold ----------
def refresh_gold(conn):
    """Full, deterministic rebuild of gold from silver (idempotent by construction)."""
    conn.execute("DELETE FROM gold_fact_order")
    conn.execute("DELETE FROM gold_dim_customer")
    conn.execute(
        "INSERT INTO gold_dim_customer SELECT customer_id, email_p, "
        "CASE WHEN dob_p GLOB '[0-9][0-9][0-9][0-9]' THEN CAST(dob_p AS INTEGER) END, "
        "country_code, substr(signup_ts,1,10), updated_at FROM silver_customers")
    conn.execute(
        "INSERT INTO gold_fact_order SELECT order_id, customer_id, substr(order_ts,1,10), order_ts, "
        "amount_cents, currency, status FROM silver_orders")


def export_gold_csv(conn, out_dir):
    """Publish gold tables as CSV (written to temp file then atomically renamed)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for table, order in (("gold_dim_customer", "customer_id"), ("gold_fact_order", "order_id")):
        cur = conn.execute(f"SELECT * FROM {table} ORDER BY {order}")
        cols = [d[0] for d in cur.description]
        target = out_dir / f"{table}.csv"
        tmp = out_dir / f".{table}.csv.tmp"
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            w.writerows(cur)
        os.replace(tmp, target)
        paths.append(str(target))
    return paths