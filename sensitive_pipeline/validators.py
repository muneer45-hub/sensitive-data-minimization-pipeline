"""Schema + business-rule validation. Pure functions: no I/O, easy to unit test."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

CUSTOMER_ID_RE = re.compile(r"^C\d{7}$")
ORDER_ID_RE = re.compile(r"^O\d{9}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
SSN_RE = re.compile(r"^\d{3}-\d{2}-\d{4}$")
PHONE_CHARS_RE = re.compile(r"^\+?[0-9\-\(\)\s\.]+$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_AGE_DAYS = 365 * 120 + 30


def normalize_ts(value: str):
    """Return ISO-8601 UTC string 'YYYY-MM-DDTHH:MM:SSZ' or None if invalid."""
    if not value or "T" not in value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean(raw: dict) -> dict:
    return {k: (v or "").strip() for k, v in raw.items()}


def validate_customer(raw: dict, cfg, today: date = None):
    """Return (reasons, clean_row). Empty reasons list == valid."""
    today = today or date.today()
    c = _clean(raw)
    reasons = []
    for col in ("customer_id", "email", "signup_ts", "updated_at", "country_code"):
        if not c.get(col):
            reasons.append(f"missing_required_field:{col}")

    if c.get("customer_id") and not CUSTOMER_ID_RE.match(c["customer_id"]):
        reasons.append("invalid_customer_id")
    if c.get("email") and not EMAIL_RE.match(c["email"]):
        reasons.append("invalid_email")
    phone = c.get("phone")
    if phone:
        digits = re.sub(r"\D", "", phone)
        if not PHONE_CHARS_RE.match(phone) or not 10 <= len(digits) <= 15:
            reasons.append("invalid_phone")
    if c.get("ssn") and not SSN_RE.match(c["ssn"]):
        reasons.append("invalid_ssn")
    dob = c.get("dob")
    if dob:
        ok = False
        if ISO_DATE_RE.match(dob):
            try:
                d = date.fromisoformat(dob)
                ok = (today - timedelta(days=MAX_AGE_DAYS)) <= d <= today
            except ValueError:
                ok = False
        if not ok:
            reasons.append("invalid_dob")
    if c.get("country_code"):
        c["country_code"] = c["country_code"].upper()
        if c["country_code"] not in cfg.allowed_country_codes:
            reasons.append("invalid_country_code")

    ts_ok = True
    for col in ("signup_ts", "updated_at"):
        if c.get(col):
            norm = normalize_ts(c[col])
            if norm is None:
                reasons.append(f"invalid_timestamp:{col}")
                ts_ok = False
            else:
                c[col] = norm
    if ts_ok and c.get("signup_ts") and c.get("updated_at") and c["updated_at"] < c["signup_ts"]:
        reasons.append("updated_before_signup")
    return reasons, c


def validate_order(raw: dict, cfg):
    """Return (reasons, clean_row). clean_row gets 'amount_cents' (int) when valid."""
    c = _clean(raw)
    reasons = []
    for col in ("order_id", "customer_id", "order_ts", "amount", "currency", "status"):
        if not c.get(col):
            reasons.append(f"missing_required_field:{col}")

    if c.get("order_id") and not ORDER_ID_RE.match(c["order_id"]):
        reasons.append("invalid_order_id")
    if c.get("customer_id") and not CUSTOMER_ID_RE.match(c["customer_id"]):
        reasons.append("invalid_customer_id")
    if c.get("order_ts"):
        norm = normalize_ts(c["order_ts"])
        if norm is None:
            reasons.append("invalid_timestamp:order_ts")
        else:
            c["order_ts"] = norm
    if c.get("amount"):
        try:
            amt = Decimal(c["amount"])
            if not amt.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            reasons.append("invalid_amount")
        else:
            if amt <= 0:
                reasons.append("invalid_amount_non_positive")
            elif amt > cfg.max_order_amount:
                reasons.append("invalid_amount_too_large")
            elif amt != amt.quantize(Decimal("0.01")):
                reasons.append("invalid_amount_precision")
            else:
                c["amount_cents"] = int(amt * 100)
    if c.get("currency"):
        c["currency"] = c["currency"].upper()
        if c["currency"] not in cfg.allowed_currencies:
            reasons.append("invalid_currency")
    if c.get("status"):
        c["status"] = c["status"].upper()
        if c["status"] not in cfg.allowed_order_statuses:
            reasons.append("invalid_status")
    return reasons, c