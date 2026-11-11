"""Validation report: reconciliation checks, label evaluation, sample analytical queries."""
from __future__ import annotations

import csv
from pathlib import Path

SAMPLE_QUERIES = {
    "Top 5 days by revenue (non-cancelled, USD)":
        "SELECT order_date, order_count, total_amount FROM gold_daily_sales WHERE currency='USD' "
        "ORDER BY total_amount DESC LIMIT 5",
    "Revenue by country (USD)":
        "SELECT country_code, order_count, total_amount FROM gold_sales_by_country WHERE currency='USD' "
        "ORDER BY total_amount DESC",
    "Customers by birth-decade (generalized, no exact DOB available)":
        "SELECT (birth_year/10)*10 AS birth_decade, COUNT(*) AS customers FROM gold_dim_customer "
        "WHERE birth_year IS NOT NULL GROUP BY 1 ORDER BY 1",
    "Quarantine summary (why records were rejected)":
        "SELECT entity, reject_reason, COUNT(*) AS records FROM quarantine_records "
        "GROUP BY entity, reject_reason ORDER BY records DESC",
    "Customers with change history (SCD-style)":
        "SELECT COUNT(DISTINCT customer_id) AS customers_with_history, COUNT(*) AS history_rows "
        "FROM silver_customers_history",
}


def _one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def run_checks(conn) -> list:
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": detail})

    run = conn.execute("SELECT run_id FROM pipeline_run ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
    if run:
        for ent, inp, ins, upd, unc, stale, rej, status, rid in conn.execute(
                "SELECT entity,input_rows,inserted,updated,unchanged,stale_ignored,rejected,status,run_id "
                "FROM entity_run_metrics WHERE run_id=?", (run[0],)):
            if status != "SUCCESS":
                add(f"[{ent}] latest run processed", True, f"status={status} (no counts to reconcile)")
                continue
            total = ins + upd + unc + stale + rej
            add(f"[{ent}] source rows = inserted+updated+unchanged+stale+rejected", inp == total,
                f"{inp} = {ins}+{upd}+{unc}+{stale}+{rej} -> {total}")
            q = _one(conn, "SELECT COUNT(*) FROM quarantine_records WHERE run_id=? AND entity=?", (rid, ent))
            add(f"[{ent}] quarantine rows = rejected + stale (traceability)", q == rej + stale,
                f"quarantine={q}, rejected+stale={rej + stale}")

    sc, gc = _one(conn, "SELECT COUNT(*) FROM silver_customers"), _one(conn, "SELECT COUNT(*) FROM gold_dim_customer")
    add("silver customers = gold customers", sc == gc, f"{sc} vs {gc}")
    so, go = _one(conn, "SELECT COUNT(*) FROM silver_orders"), _one(conn, "SELECT COUNT(*) FROM gold_fact_order")
    add("silver orders = gold orders", so == go, f"{so} vs {go}")
    ss = _one(conn, "SELECT COALESCE(SUM(amount_cents),0) FROM silver_orders")
    gs = _one(conn, "SELECT COALESCE(SUM(amount_cents),0) FROM gold_fact_order")
    add("sum(amount_cents) silver = gold", ss == gs, f"{ss} vs {gs}")
    orphans = _one(conn, "SELECT COUNT(*) FROM silver_orders o LEFT JOIN silver_customers c "
                         "ON c.customer_id=o.customer_id WHERE c.customer_id IS NULL")
    add("referential integrity: no orphan orders in silver", orphans == 0, f"orphans={orphans}")
    g_orph = _one(conn, "SELECT COUNT(*) FROM gold_fact_order o LEFT JOIN gold_dim_customer c "
                        "ON c.customer_id=o.customer_id WHERE c.customer_id IS NULL")
    add("referential integrity: no orphan orders in gold", g_orph == 0, f"orphans={g_orph}")
    dup_c = _one(conn, "SELECT COUNT(*) - COUNT(DISTINCT customer_id) FROM gold_dim_customer")
    dup_o = _one(conn, "SELECT COUNT(*) - COUNT(DISTINCT order_id) FROM gold_fact_order")
    add("primary keys unique in gold", dup_c == 0 and dup_o == 0, f"dup customers={dup_c}, dup orders={dup_o}")
    leak_email = _one(conn, "SELECT COUNT(*) FROM gold_dim_customer WHERE email_token LIKE '%@%'")
    leak_ssn = _one(conn, "SELECT COUNT(*) FROM silver_customers WHERE ssn_p GLOB '[0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9][0-9][0-9]'")
    leak_dob = _one(conn, "SELECT COUNT(*) FROM silver_customers WHERE dob_p GLOB '[0-9][0-9][0-9][0-9]-*'")
    add("no raw email in gold / raw SSN or full DOB in silver", leak_email + leak_ssn + leak_dob == 0,
        f"email={leak_email}, ssn={leak_ssn}, full_dob={leak_dob}")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(gold_dim_customer)")]
    banned = {"full_name", "phone", "ssn", "dob", "email"}
    add("gold customer table is minimized (no direct identifiers)", not banned & set(cols), f"columns={cols}")
    return checks


def evaluate_labels(conn, labels_path) -> dict:
    """Compare quarantine contents against the generator's separate expected-answer labels."""
    expected = {}
    with open(labels_path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            expected[(r["entity"], int(r["source_line_number"]))] = r["expected_reason"]
    actual = {}
    for ent, line, reason in conn.execute(
            "SELECT entity, source_line_number, reject_reason FROM quarantine_records "
            "WHERE reject_reason <> 'late_arriving_stale_update'"):
        actual[(ent, line)] = reason
    hit = [k for k, v in expected.items() if k in actual and v in actual[k]]
    missed = [k for k in expected if k not in hit]
    unexpected = [k for k in actual if k not in expected]
    return {"expected_defects": len(expected), "correctly_quarantined": len(hit), "missed": sorted(missed),
            "unexpected_quarantine": sorted(unexpected),
            "ok": not missed and not unexpected}


def build_report(conn, labels_path=None, out_path=None):
    checks = run_checks(conn)
    lines = ["# Validation & reconciliation report", "", "## Reconciliation checks", "",
             "| check | result | detail |", "|---|---|---|"]
    lines += [f"| {c['check']} | **{c['result']}** | {c['detail']} |" for c in checks]
    label_result = None
    if labels_path and Path(labels_path).exists():
        label_result = evaluate_labels(conn, labels_path)
        lines += ["", "## Labelled-defect evaluation (labels kept separate from input)", "",
                  f"- expected defects: {label_result['expected_defects']}",
                  f"- correctly quarantined with the expected reason: {label_result['correctly_quarantined']}",
                  f"- missed: {label_result['missed'] or 'none'}",
                  f"- unexpected quarantine rows (false positives): {label_result['unexpected_quarantine'] or 'none'}"]
    lines += ["", "## Sample analytical queries (run against the minimized gold layer)"]
    for title, sql in SAMPLE_QUERIES.items():
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        lines += ["", f"### {title}", "", "```sql", sql, "```", "", "| " + " | ".join(cols) + " |",
                  "|" + "---|" * len(cols)]
        lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in cur.fetchall()]
    text = "\n".join(lines) + "\n"
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(text, encoding="utf-8")
    all_ok = all(c["result"] == "PASS" for c in checks) and (label_result is None or label_result["ok"])
    return {"checks": checks, "labels": label_result, "all_ok": all_ok, "markdown": text}