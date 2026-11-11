"""Sensitive-field profiler: finds PII by column-name hints AND value patterns, checks policy coverage."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .schema import ENTITY_SPECS
from .validators import EMAIL_RE, SSN_RE

PHONE_RE = re.compile(r"^\+?[\d\-\(\)\s\.]{10,18}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

NAME_HINTS = [("email", "email"), ("phone", "phone"), ("mobile", "phone"), ("ssn", "national_id"),
              ("dob", "date_of_birth"), ("birth", "date_of_birth"), ("name", "person_name"),
              ("address", "address")]
VALUE_PATTERNS = [("email", EMAIL_RE), ("national_id", SSN_RE), ("phone", PHONE_RE)]
SENSITIVITY = {"national_id": "high", "date_of_birth": "high", "email": "medium", "phone": "medium",
               "person_name": "medium", "address": "medium"}
SAMPLE_LIMIT = 50000


def _detect(column: str, values: list):
    hint = next((t for h, t in NAME_HINTS if h in column.lower()), None)
    non_null = [v for v in values if v]
    by_value = None
    if non_null:
        for ptype, rx in VALUE_PATTERNS:
            if sum(1 for v in non_null if rx.match(v)) / len(non_null) >= 0.5:
                by_value = ptype
                break
    return hint or by_value, ("name_hint+value_pattern" if hint and by_value else
                              "name_hint" if hint else "value_pattern" if by_value else None)


def profile_file(path, entity: str, policy: dict) -> dict:
    path = Path(path)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
        data = {h: [] for h in header}
        n = 0
        for row in reader:
            if not row or len(row) != len(header):
                continue
            n += 1
            if n <= SAMPLE_LIMIT:
                for h, v in zip(header, row):
                    data[h].append(v.strip())
    known = set(ENTITY_SPECS[entity]["required"] + ENTITY_SPECS[entity]["optional"])
    columns = []
    for col in header:
        vals = data[col]
        pii, evidence = _detect(col, vals)
        action = policy.get(col)
        if pii is None:
            status = "not_sensitive"
        elif action and action != "keep":
            status = "PROTECTED"
        elif col not in known:
            status = "UNKNOWN_COLUMN_NEVER_PUBLISHED"   # unknown columns are never carried forward
        else:
            status = "UNPROTECTED_SENSITIVE"
        columns.append({
            "column": col,
            "null_rate": round(sum(1 for v in vals if not v) / len(vals), 4) if vals else None,
            "distinct_values": len(set(vals)),
            "detected_pii_type": pii, "evidence": evidence,
            "sensitivity": SENSITIVITY.get(pii, "none"),
            "configured_action": action, "status": status,
        })
    return {"entity": entity, "file": path.name, "rows_profiled": min(n, SAMPLE_LIMIT), "rows_total": n,
            "columns": columns,
            "warnings": [c["column"] for c in columns if c["status"] == "UNPROTECTED_SENSITIVE"]}


def write_profile_reports(profiles: list, out_dir) -> list:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "profile_report.json").write_text(json.dumps(profiles, indent=2), encoding="utf-8")
    lines = ["# Sensitive field profile", ""]
    for p in profiles:
        lines += [f"## {p['entity']} ({p['file']}, {p['rows_total']} rows)", "",
                  "| column | null_rate | distinct | detected_pii | sensitivity | action | status |",
                  "|---|---|---|---|---|---|---|"]
        for c in p["columns"]:
            lines.append(f"| {c['column']} | {c['null_rate']} | {c['distinct_values']} | {c['detected_pii_type']} | "
                         f"{c['sensitivity']} | {c['configured_action']} | {c['status']} |")
        lines += ["", f"Unprotected sensitive columns: {p['warnings'] or 'none'}", ""]
    (out_dir / "profile_report.md").write_text("\n".join(lines), encoding="utf-8")
    return [str(out_dir / "profile_report.json"), str(out_dir / "profile_report.md")]