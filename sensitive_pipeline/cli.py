"""Command line interface:  python -m sensitive_pipeline.cli <command> [options]"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .benchmark import run_benchmark
from .config import load_config
from .generator import generate
from .pipeline import run_pipeline
from .profiler import profile_file, write_profile_reports
from .reconcile import build_report
from .schema import ENTITY_SPECS, PROCESSING_ORDER
from . import storage


def _setup_logging(output_dir: Path):
    (output_dir / "logs").mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger("sensitive_pipeline")
    root.setLevel(logging.INFO)
    root.handlers.clear()
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(output_dir / "logs" / "pipeline.log")):
        h.setFormatter(fmt)
        root.addHandler(h)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sensitive_pipeline")
    p.add_argument("--config", default="config/pipeline_config.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate reproducible synthetic data + separate labels")
    g.add_argument("--out", default="sample_data")
    g.add_argument("--customers", type=int, default=200)
    g.add_argument("--orders", type=int, default=600)
    g.add_argument("--seed", type=int, default=42)

    for name in ("profile", "run", "report"):
        s = sub.add_parser(name)
        s.add_argument("--input-dir")
        s.add_argument("--output-dir")
    sub.choices["run"].add_argument("--force", action="store_true", help="reprocess files even if already loaded")
    sub.choices["run"].add_argument("--run-id")
    sub.choices["report"].add_argument("--labels", default="sample_data/labels/expected_labels.csv")

    b = sub.add_parser("benchmark")
    b.add_argument("--customers", type=int, default=100000)
    b.add_argument("--orders", type=int, default=300000)
    b.add_argument("--output-dir")

    a = p.parse_args(argv)

    if a.cmd == "generate":
        print(json.dumps(generate(a.out, a.customers, a.orders, a.seed), indent=2))
        return 0

    cfg = load_config(a.config, getattr(a, "input_dir", None), getattr(a, "output_dir", None))
    _setup_logging(cfg.output_dir)

    if a.cmd == "profile":
        profiles = [profile_file(cfg.input_dir / ENTITY_SPECS[e]["file"], e, cfg.policies.get(e, {}))
                    for e in PROCESSING_ORDER]
        print("\n".join(write_profile_reports(profiles, cfg.output_dir / "reports")))
        unprotected = [(p_["entity"], c) for p_ in profiles for c in p_["warnings"]]
        if unprotected:
            print("WARNING unprotected sensitive columns:", unprotected)
        return 0

    if a.cmd == "run":
        summary = run_pipeline(cfg, run_id=a.run_id, force=a.force)
        print(json.dumps(summary, indent=2))
        return 0 if summary["status"] == "SUCCESS" else 1

    if a.cmd == "report":
        conn = storage.connect(cfg.db_path)
        result = build_report(conn, a.labels, cfg.output_dir / "reports" / "validation_report.md")
        for c in result["checks"]:
            print(f"[{c['result']}] {c['check']} -> {c['detail']}")
        if result["labels"]:
            print("labels:", {k: v for k, v in result["labels"].items() if k != "ok"})
        print("ALL CHECKS PASSED" if result["all_ok"] else "SOME CHECKS FAILED")
        return 0 if result["all_ok"] else 1

    if a.cmd == "benchmark":
        out = cfg.output_dir / "reports" / "performance.md"
        print(json.dumps(run_benchmark(a.config, a.customers, a.orders, out), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())