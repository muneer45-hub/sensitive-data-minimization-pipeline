"""Core pipeline: bronze landing -> silver (validate/protect/upsert/quarantine) -> gold (minimized)."""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import shutil
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from . import storage
from .protection import Protector, protect_record, redact_record
from .schema import ENTITY_SPECS, PROCESSING_ORDER, all_columns
from .validators import validate_customer, validate_order

log = logging.getLogger("sensitive_pipeline")


class SchemaError(Exception):
    """Raised when a file's header does not satisfy the expected schema."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _row_hash(*parts) -> str:
    return hashlib.sha256("\x1f".join("" if p is None else str(p) for p in parts).encode()).hexdigest()


class _Ctx:
    """Bundle of per-entity-load state so helpers stay small."""

    def __init__(self, conn, cfg, run_id, entity, source_file, sha):
        self.conn, self.cfg, self.run_id, self.entity = conn, cfg, run_id, entity
        self.source_file, self.sha, self.ts = source_file, sha, utc_now()
        self.policy = cfg.policies.get(entity, {})
        self.stats = Counter()
        self.reasons = Counter()
        self.protect_counts = Counter()

    def quarantine(self, line_no, key, reasons, raw, stale=False):
        redacted = raw if isinstance(raw, dict) and "_malformed" in raw else \
            redact_record(raw, all_columns(self.entity), self.policy)
        storage.write_quarantine(self.conn, self.run_id, self.entity, self.source_file, self.sha, line_no,
                                 key, ";".join(reasons), redacted, self.ts)
        if stale:
            self.stats["stale_ignored"] += 1
        else:
            self.stats["rejected"] += 1
        for r in reasons:
            self.reasons[r] += 1


def _read_rows(path: Path):
    """Returns (header or None, [(line_number, values)]). Skips blank lines."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            return None, []
        rows = []
        for values in reader:
            if not values:
                continue
            rows.append((reader.line_num, values))
    return [h.strip() for h in header], rows


def _check_header(entity, header):
    spec = ENTITY_SPECS[entity]
    if len(set(header)) != len(header):
        raise SchemaError("duplicate_column_names_in_header")
    missing = [c for c in spec["required"] if c not in header]
    if missing:
        raise SchemaError("missing_required_columns:" + ",".join(missing))
    return [c for c in header if c not in all_columns(entity)]  # unexpected extras


def _load_customers(ctx: _Ctx, header, rows, protector, today):
    parsed = []
    for line_no, values in rows:
        ctx.stats["input_rows"] += 1
        if len(values) != len(header):
            ctx.quarantine(line_no, None, ["malformed_row"], {"_malformed": f"{len(values)} fields"})
            continue
        raw = dict(zip(header, values))
        reasons, clean = validate_customer(raw, ctx.cfg, today)
        if reasons:
            ctx.quarantine(line_no, clean.get("customer_id") or None, reasons, raw)
            continue
        prot = protect_record(clean, ctx.policy, protector)
        rec = {c: prot.get(c) for c in ("full_name_p", "email_p", "phone_p", "ssn_p", "dob_p")}
        rec.update(customer_id=clean["customer_id"], country_code=clean["country_code"],
                   signup_ts=clean["signup_ts"], updated_at=clean["updated_at"],
                   run_id=ctx.run_id, source_file=ctx.source_file)
        rec["row_hash"] = _row_hash(rec["customer_id"], rec["full_name_p"], rec["email_p"], rec["phone_p"],
                                    rec["ssn_p"], rec["dob_p"], rec["country_code"], rec["signup_ts"],
                                    rec["updated_at"])
        for field in ctx.policy:
            if clean.get(field):
                ctx.protect_counts[field] += 1
        parsed.append((rec["updated_at"], line_no, rec, raw))

    # out-of-order data inside a batch is applied in event-time order
    parsed.sort(key=lambda r: (r[0], r[1]))
    seen = set()
    for updated_at, line_no, rec, raw in parsed:
        key = rec["customer_id"]
        if (key, rec["row_hash"]) in seen:
            ctx.quarantine(line_no, key, ["duplicate_record"], raw)
            continue
        seen.add((key, rec["row_hash"]))
        state = storage.get_customer_state(ctx.conn, key)
        if state is None:
            storage.insert_customer(ctx.conn, rec)
            ctx.stats["inserted"] += 1
        elif updated_at < state[0]:
            ctx.quarantine(line_no, key, ["late_arriving_stale_update"], raw, stale=True)
        elif updated_at == state[0] and rec["row_hash"] == state[1]:
            ctx.stats["unchanged"] += 1
        else:
            storage.archive_customer(ctx.conn, key, ctx.run_id, ctx.ts)  # keep change history
            storage.update_customer(ctx.conn, rec)
            ctx.stats["updated"] += 1


def _load_orders(ctx: _Ctx, header, rows):
    customer_ids = storage.customer_id_set(ctx.conn)
    seen = {}
    for line_no, values in rows:
        ctx.stats["input_rows"] += 1
        if len(values) != len(header):
            ctx.quarantine(line_no, None, ["malformed_row"], {"_malformed": f"{len(values)} fields"})
            continue
        raw = dict(zip(header, values))
        reasons, clean = validate_order(raw, ctx.cfg)
        if reasons:
            ctx.quarantine(line_no, clean.get("order_id") or None, reasons, raw)
            continue
        if clean["customer_id"] not in customer_ids:
            ctx.quarantine(line_no, clean["order_id"], ["invalid_reference_key"], raw)
            continue
        rec = {k: clean[k] for k in ("order_id", "customer_id", "order_ts", "amount_cents", "currency", "status")}
        rec.update(run_id=ctx.run_id, source_file=ctx.source_file)
        rec["row_hash"] = _row_hash(rec["order_id"], rec["customer_id"], rec["order_ts"], rec["amount_cents"],
                                    rec["currency"], rec["status"])
        key = rec["order_id"]
        if key in seen:
            reason = "duplicate_record" if seen[key] == rec["row_hash"] else "conflicting_duplicate_key"
            ctx.quarantine(line_no, key, [reason], raw)
            continue
        seen[key] = rec["row_hash"]
        existing = storage.get_order_hash(ctx.conn, key)
        if existing is None:
            storage.insert_order(ctx.conn, rec)
            ctx.stats["inserted"] += 1
        elif existing == rec["row_hash"]:
            ctx.stats["unchanged"] += 1
        else:
            ctx.quarantine(line_no, key, ["conflicting_duplicate_key"], raw)


def _load_entity(conn, cfg, run_id, entity, path, sha, today):
    """Runs inside a transaction opened by the caller. Raises on schema problems."""
    ctx = _Ctx(conn, cfg, run_id, entity, path.name, sha)
    header, rows = _read_rows(path)
    if header is None or not rows:
        log.warning("[%s] %s: empty batch (%s)", run_id, entity, path.name)
        storage.audit(conn, run_id, ctx.ts, "silver", entity, None, "empty_batch", 0, {"file": path.name})
        return ctx.stats
    extras = _check_header(entity, header)
    if extras:
        log.warning("[%s] %s: unexpected extra columns ignored: %s", run_id, entity, extras)
        storage.audit(conn, run_id, ctx.ts, "silver", entity, None, "schema_drift_extra_columns_ignored",
                      0, {"columns": extras})
    if entity == "customers":
        _load_customers(ctx, header, rows, Protector(cfg.token_key), today)
    else:
        _load_orders(ctx, header, rows)

    for field, action in ctx.policy.items():
        storage.audit(conn, run_id, ctx.ts, "silver", entity, field, action, ctx.protect_counts[field])
    if ctx.reasons:
        storage.audit(conn, run_id, ctx.ts, "silver", entity, None, "reject_reason_counts",
                      ctx.stats["rejected"] + ctx.stats["stale_ignored"], dict(ctx.reasons))
    return ctx.stats


def _process_entity(conn, cfg, run_id, entity, path, force, today):
    m = dict(run_id=run_id, entity=entity, source_file=path.name, status="RUNNING", input_rows=0, inserted=0,
             updated=0, unchanged=0, stale_ignored=0, rejected=0, failure_reason=None, duration_s=0.0)
    t0 = time.perf_counter()
    sha, landed = None, None
    try:
        if not path.exists():
            raise FileNotFoundError(f"input_file_missing:{path}")
        sha = sha256_file(path)
        if not force and storage.already_processed(conn, entity, sha):
            m["status"] = "SKIPPED_ALREADY_PROCESSED"
            log.info("[%s] %s: identical file already processed (sha256=%s...), skipping", run_id, entity, sha[:12])
        else:
            dest = cfg.output_dir / "bronze" / run_id
            dest.mkdir(parents=True, exist_ok=True)
            landed = str(dest / path.name)
            shutil.copy2(path, landed)                     # bronze: immutable raw copy
            storage.begin(conn)
            try:
                stats = _load_entity(conn, cfg, run_id, entity, path, sha, today)
                storage.commit(conn)
            except BaseException:
                storage.rollback(conn)                      # all-or-nothing per entity
                raise
            for k in ("input_rows", "inserted", "updated", "unchanged", "stale_ignored", "rejected"):
                m[k] = stats.get(k, 0)
            m["status"] = "SUCCESS"
    except Exception as exc:                                # noqa: BLE001 - recorded, never swallowed silently
        m["status"] = "FAILED"
        m["failure_reason"] = f"{type(exc).__name__}: {exc}"
        log.error("[%s] %s FAILED: %s", run_id, entity, m["failure_reason"])
    m["duration_s"] = round(time.perf_counter() - t0, 4)
    if sha:
        storage.write_manifest(conn, run_id, entity, path.name, sha, m["input_rows"], landed, m["status"])
    storage.write_entity_metrics(conn, m)
    log.info("[%s] %s: status=%s in=%d inserted=%d updated=%d unchanged=%d stale=%d rejected=%d (%.2fs)",
             run_id, entity, m["status"], m["input_rows"], m["inserted"], m["updated"], m["unchanged"],
             m["stale_ignored"], m["rejected"], m["duration_s"])
    return m


def run_pipeline(cfg, run_id=None, force=False, today=None) -> dict:
    """Run the full pipeline once. Never raises for data problems; returns a summary dict."""
    today = today or date.today()
    t0 = time.perf_counter()
    started = utc_now()
    run_id = run_id or "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    conn = storage.connect(cfg.db_path)
    storage.init_schema(conn)
    storage.start_run(conn, run_id, started)
    if cfg.using_dev_key:
        log.warning("[%s] PII_TOKEN_KEY not set: using insecure DEV key (fine for demo, never for production)", run_id)
    log.info("[%s] run started (force=%s, input_dir=%s)", run_id, force, cfg.input_dir)

    results = [_process_entity(conn, cfg, run_id, e, cfg.input_dir / ENTITY_SPECS[e]["file"], force, today)
               for e in PROCESSING_ORDER]

    failure = None
    try:
        if any(r["status"] == "SUCCESS" for r in results):
            storage.begin(conn)
            try:
                storage.refresh_gold(conn)
                storage.audit(conn, run_id, utc_now(), "gold", None, None, "refresh_gold_minimized", 0,
                              {"customer_columns": ["customer_id", "email_token", "birth_year", "country_code",
                                                    "signup_date", "last_updated_at"],
                               "dropped_from_gold": ["full_name", "phone", "ssn", "dob(full date)"]})
                storage.commit(conn)
            except BaseException:
                storage.rollback(conn)
                raise
            storage.export_gold_csv(conn, cfg.output_dir / "published")
    except Exception as exc:                                # noqa: BLE001
        failure = f"gold_refresh_failed: {type(exc).__name__}: {exc}"
        log.error("[%s] %s", run_id, failure)

    failed = [r for r in results if r["status"] == "FAILED"]
    if failure or len(failed) == len(results):
        status = "FAILED"
    elif failed:
        status = "PARTIAL_FAILURE"
    else:
        status = "SUCCESS"
    reasons = [f"{r['entity']}: {r['failure_reason']}" for r in failed]
    if failure:
        reasons.append(failure)

    totals = {
        "input_rows": sum(r["input_rows"] for r in results),
        "loaded_rows": sum(r["inserted"] + r["updated"] for r in results),
        "rejected_rows": sum(r["rejected"] for r in results),
        "stale_ignored_rows": sum(r["stale_ignored"] for r in results),
    }
    duration = round(time.perf_counter() - t0, 4)
    ended = utc_now()
    storage.finish_run(conn, run_id, ended, status, " | ".join(reasons) or None, totals, duration)
    summary = {"run_id": run_id, "status": status, "started_at": started, "ended_at": ended,
               "duration_s": duration, "failure_reason": " | ".join(reasons) or None,
               "totals": totals, "entities": results}
    reports = cfg.output_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"run_{run_id}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    conn.close()
    log.info("[%s] run finished: status=%s totals=%s duration=%.2fs", run_id, status, totals, duration)
    return summary