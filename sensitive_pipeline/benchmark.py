"""Simple performance measurement + one concrete optimization (single transaction vs autocommit)."""
from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

from .config import load_config
from .generator import generate
from .pipeline import run_pipeline


def micro_insert_benchmark(n_txn=50000, n_autocommit=2000) -> dict:
    """Same INSERT workload: one commit per row vs one transaction for the whole batch."""
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        for mode, n in (("autocommit_per_row", n_autocommit), ("single_transaction", n_txn)):
            conn = sqlite3.connect(str(Path(tmp) / f"{mode}.db"), isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            t0 = time.perf_counter()
            if mode == "single_transaction":
                conn.execute("BEGIN")
            for i in range(n):
                conn.execute("INSERT INTO t VALUES (?,?)", (i, "x" * 20))
            if mode == "single_transaction":
                conn.execute("COMMIT")
            dt = time.perf_counter() - t0
            results[mode] = {"rows": n, "seconds": round(dt, 3), "rows_per_sec": round(n / dt)}
            conn.close()
    results["speedup_x"] = round(results["single_transaction"]["rows_per_sec"] /
                                 max(results["autocommit_per_row"]["rows_per_sec"], 1), 1)
    return results


def run_benchmark(config_path, customers=100000, orders=300000, out_report=None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        t0 = time.perf_counter()
        generate(tmp / "data", customers, orders, seed=7)
        gen_s = time.perf_counter() - t0
        cfg = load_config(config_path, input_dir=tmp / "data" / "input", output_dir=tmp / "out")
        summary = run_pipeline(cfg, run_id="bench_run")
    micro = micro_insert_benchmark()
    rows = summary["totals"]["input_rows"]
    result = {"generated_customers": customers, "generated_orders": orders, "generator_seconds": round(gen_s, 2),
              "pipeline_seconds": summary["duration_s"], "input_rows": rows,
              "pipeline_rows_per_sec": round(rows / max(summary["duration_s"], 1e-9)),
              "per_entity": {e["entity"]: {"input_rows": e["input_rows"], "seconds": e["duration_s"]}
                             for e in summary["entities"]},
              "insert_strategy_microbenchmark": micro}
    if out_report:
        lines = ["# Performance measurement", "",
                 f"- Dataset: {customers:,} customers + {orders:,} orders (seed=7)",
                 f"- Generator: {result['generator_seconds']} s",
                 f"- Full pipeline (bronze->silver->gold + CSV publish): {result['pipeline_seconds']} s "
                 f"({result['pipeline_rows_per_sec']:,} input rows/s)", ""]
        for e, v in result["per_entity"].items():
            lines.append(f"- {e}: {v['input_rows']:,} rows in {v['seconds']} s")
        lines += ["", "## Optimization: one transaction per entity instead of one commit per row", "",
                  f"- autocommit per row: {micro['autocommit_per_row']['rows_per_sec']:,} rows/s",
                  f"- single transaction: {micro['single_transaction']['rows_per_sec']:,} rows/s",
                  f"- speed-up: ~{micro['speedup_x']}x", "",
                  "The pipeline uses the single-transaction strategy (and it also gives all-or-nothing rollback)."]
        Path(out_report).parent.mkdir(parents=True, exist_ok=True)
        Path(out_report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result