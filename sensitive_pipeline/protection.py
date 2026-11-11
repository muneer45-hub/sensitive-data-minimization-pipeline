"""Masking / tokenization actions. Deterministic: same input + key -> same output."""
from __future__ import annotations

import hashlib
import hmac
import re


class Protector:
    def __init__(self, key: str):
        self._key = key.encode("utf-8")

    def tokenize(self, value: str) -> str:
        """HMAC-SHA256 token: stable (joinable) but not reversible without the key."""
        norm = value.strip().lower()
        digest = hmac.new(self._key, norm.encode("utf-8"), hashlib.sha256).hexdigest()
        return "tok_" + digest[:24]

    def apply(self, action: str, value):
        if value is None or value == "":
            return None
        if action == "drop":
            return None
        if action == "keep":
            return value
        if action == "tokenize":
            return self.tokenize(value)
        if action == "mask_email":
            local, _, domain = value.partition("@")
            return f"{local[:1]}***@{domain}"
        if action == "mask_phone":
            digits = re.sub(r"\D", "", value)
            return "*" * max(len(digits) - 4, 0) + digits[-4:]
        if action == "mask_ssn":
            return "***-**-" + value[-4:]
        if action == "generalize_dob":
            return value[:4]  # birth year only
        raise ValueError(f"unknown action {action}")


def protect_record(clean: dict, policy: dict, protector: Protector) -> dict:
    """Returns {'<field>_p': protected_value} for every field in the policy."""
    return {f"{field}_p": protector.apply(action, clean.get(field)) for field, action in policy.items()}


def redact_record(raw: dict, entity_columns: list, policy: dict) -> dict:
    """Copy of a raw record that is safe to store in quarantine (no PII, no unknown columns)."""
    out = {}
    for col in entity_columns:
        if col not in raw:
            continue
        sensitive = policy.get(col, "keep") != "keep" and (raw[col] or "") != ""
        out[col] = "[REDACTED]" if sensitive else raw[col]
    return out