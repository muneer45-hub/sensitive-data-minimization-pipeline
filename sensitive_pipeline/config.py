"""Configuration loading and validation (policies are data, not code)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .schema import ENTITY_SPECS

VALID_ACTIONS = {"keep", "drop", "tokenize", "mask_email", "mask_phone", "mask_ssn", "generalize_dob"}
DEV_KEY = "dev-only-insecure-key-do-not-use-in-production"


@dataclass
class Config:
    input_dir: Path
    output_dir: Path
    db_path: Path
    policies: dict
    token_key: str
    using_dev_key: bool
    allowed_country_codes: frozenset
    allowed_currencies: frozenset
    allowed_order_statuses: frozenset
    max_order_amount: Decimal


def load_config(path="config/pipeline_config.json", input_dir=None, output_dir=None) -> Config:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)

    policies = raw.get("policies", {})
    for entity, fields in policies.items():
        if entity not in ENTITY_SPECS:
            raise ValueError(f"policy refers to unknown entity '{entity}'")
        known = set(ENTITY_SPECS[entity]["required"] + ENTITY_SPECS[entity]["optional"])
        for field, action in fields.items():
            if field not in known:
                raise ValueError(f"policy refers to unknown field '{entity}.{field}'")
            if action not in VALID_ACTIONS:
                raise ValueError(f"invalid action '{action}' for {entity}.{field}; valid: {sorted(VALID_ACTIONS)}")

    key = os.environ.get(raw.get("token_key_env_var", "PII_TOKEN_KEY"))
    out = Path(output_dir or raw["paths"]["output_dir"])
    return Config(
        input_dir=Path(input_dir or raw["paths"]["input_dir"]),
        output_dir=out,
        db_path=out / raw["paths"]["db_filename"],
        policies=policies,
        token_key=key or DEV_KEY,
        using_dev_key=not key,
        allowed_country_codes=frozenset(raw["allowed_country_codes"]),
        allowed_currencies=frozenset(raw["allowed_currencies"]),
        allowed_order_statuses=frozenset(raw["allowed_order_statuses"]),
        max_order_amount=Decimal(raw["max_order_amount"]),
    )