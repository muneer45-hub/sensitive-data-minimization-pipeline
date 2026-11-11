"""Reproducible synthetic data generator (fixed seed). Expected-answer labels go to a SEPARATE folder."""
from __future__ import annotations

import csv
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FIRST = ["Aarav", "Maya", "Liam", "Sofia", "Noah", "Emma", "Ravi", "Zoe", "Ethan", "Priya", "Lucas", "Anna"]
LAST = ["Sharma", "Smith", "Garcia", "Kumar", "Brown", "Muller", "Patel", "Jones", "Lee", "Rossi"]
DOMAINS = ["example.com", "mail.test", "corp.example", "demo.org"]
COUNTRIES = ["US", "GB", "IN", "DE", "CA", "AU", "FR", "SG"]
CURRENCIES = ["USD", "EUR", "GBP", "INR"]
STATUSES = ["PLACED", "SHIPPED", "CANCELLED", "REFUNDED"]
CUSTOMER_COLS = ["customer_id", "full_name", "email", "phone", "ssn", "dob", "country_code", "signup_ts", "updated_at"]
ORDER_COLS = ["order_id", "customer_id", "order_ts", "amount", "currency", "status"]

CUSTOMER_DEFECTS = {
    "missing_email": ("email", "", "missing_required_field:email"),
    "bad_email": ("email", "not-an-email", "invalid_email"),
    "bad_ssn": ("ssn", "123456789", "invalid_ssn"),
    "future_dob": ("dob", "2099-01-01", "invalid_dob"),
    "bad_country": ("country_code", "ZZ", "invalid_country_code"),
    "bad_timestamp": ("signup_ts", "01/02/2024 10:00", "invalid_timestamp"),
    "missing_customer_id": ("customer_id", "", "missing_required_field:customer_id"),
}
ORDER_DEFECTS = {
    "missing_order_id": ("order_id", "", "missing_required_field:order_id"),
    "negative_amount": ("amount", "-5.00", "invalid_amount_non_positive"),
    "zero_amount": ("amount", "0.00", "invalid_amount_non_positive"),
    "non_numeric_amount": ("amount", "abc", "invalid_amount"),
    "bad_currency": ("currency", "XXX", "invalid_currency"),
    "bad_status": ("status", "UNKNOWN", "invalid_status"),
    "bad_order_ts": ("order_ts", "2024-13-45T00:00:00Z", "invalid_timestamp"),
    "orphan_customer": ("customer_id", "C9999999", "invalid_reference_key"),
}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def generate(out_dir, n_customers=200, n_orders=600, seed=42, defect_rate=0.04):
    rng = random.Random(seed)
    out_dir = Path(out_dir)
    (out_dir / "input").mkdir(parents=True, exist_ok=True)
    (out_dir / "labels").mkdir(parents=True, exist_ok=True)
    base = datetime(2023, 1, 1, tzinfo=timezone.utc)

    # ---------------- customers ----------------
    c_rows, c_labels, good_ids = [], [], []   # c_labels[i] is (defect_type, reason) or None for c_rows[i]
    keys = list(CUSTOMER_DEFECTS)
    for i in range(1, n_customers + 1):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        signup = base + timedelta(seconds=rng.randint(0, 700 * 86400))
        row = {
            "customer_id": f"C{i:07d}",
            "full_name": f"{first} {last}",
            "email": f"{first}.{last}.{i}@{rng.choice(DOMAINS)}".lower(),
            "phone": f"+1{rng.randint(2000000000, 9999999999)}" if rng.random() > 0.1 else "",  # optional -> nulls
            "ssn": f"{rng.randint(100, 899)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}",
            "dob": (date(1950, 1, 1) + timedelta(days=rng.randint(0, 20000))).isoformat(),
            "country_code": rng.choice(COUNTRIES),
            "signup_ts": _iso(signup),
            "updated_at": _iso(signup + timedelta(seconds=rng.randint(0, 30 * 86400))),
        }
        if rng.random() < defect_rate:
            name = rng.choice(keys)
            col, bad, reason = CUSTOMER_DEFECTS[name]
            row[col] = bad
            c_rows.append(row)
            c_labels.append((name, reason))
            continue
        good_ids.append(row["customer_id"])
        if rng.random() < 0.03:  # out-of-order change: newer version appears BEFORE the original in the file
            upd = dict(row)
            upd["phone"] = f"+1{rng.randint(2000000000, 9999999999)}"
            upd["updated_at"] = _iso(datetime.strptime(row["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
                                     .replace(tzinfo=timezone.utc) + timedelta(days=10))
            c_rows.append(upd)
            c_labels.append(None)
        c_rows.append(row)
        c_labels.append(None)
    # exact duplicates appended at the end
    for src in [r for r, l in zip(c_rows, c_labels) if l is None][:: max(1, n_customers // 10)][:max(1, n_customers // 20)]:
        c_rows.append(dict(src))
        c_labels.append(("duplicate_customer", "duplicate_record"))

    # ---------------- orders ----------------
    o_rows, o_labels = [], []
    okeys = list(ORDER_DEFECTS)
    for j in range(1, n_orders + 1):
        amount = round(rng.uniform(5, 900), 2)
        if j % 97 == 0:
            amount = 0.01            # boundary: minimum valid
        elif j % 193 == 0:
            amount = 1000000.00      # boundary: maximum valid
        row = {
            "order_id": f"O{j:09d}",
            "customer_id": rng.choice(good_ids),
            "order_ts": _iso(base + timedelta(seconds=rng.randint(0, 1000 * 86400))),
            "amount": f"{amount:.2f}",
            "currency": rng.choice(CURRENCIES),
            "status": rng.choice(STATUSES),
        }
        if rng.random() < defect_rate:
            name = rng.choice(okeys)
            col, bad, reason = ORDER_DEFECTS[name]
            row[col] = bad
            o_rows.append(row)
            o_labels.append((name, reason))
        else:
            o_rows.append(row)
            o_labels.append(None)
    clean_orders = [r for r, l in zip(o_rows, o_labels) if l is None]
    for k, src in enumerate(clean_orders[:: max(1, n_orders // 10)][:max(2, n_orders // 30)]):
        dup = dict(src)
        if k % 2:
            dup["amount"] = f"{float(dup['amount']) + 1:.2f}"
            if float(dup["amount"]) > 1000000:
                dup["amount"] = "1.00"
            o_rows.append(dup)
            o_labels.append(("conflicting_duplicate_order", "conflicting_duplicate_key"))
        else:
            o_rows.append(dup)
            o_labels.append(("duplicate_order", "duplicate_record"))

    _write_csv(out_dir / "input" / "customers.csv", CUSTOMER_COLS, c_rows)
    _write_csv(out_dir / "input" / "orders.csv", ORDER_COLS, o_rows)
    with open(out_dir / "labels" / "expected_labels.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["entity", "source_line_number", "defect_type", "expected_outcome", "expected_reason"])
        for entity, labels in (("customers", c_labels), ("orders", o_labels)):
            for idx, lab in enumerate(labels):
                if lab:
                    w.writerow([entity, idx + 2, lab[0], "quarantine", lab[1]])   # line 1 is the header
    manifest = {"seed": seed, "customers_rows": len(c_rows), "orders_rows": len(o_rows),
                "labelled_defects": sum(1 for l in c_labels + o_labels if l)}
    (out_dir / "labels" / "generation_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _write_csv(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)