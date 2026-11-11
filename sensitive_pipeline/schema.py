"""Source schemas: one place that defines entities, keys and columns."""

ENTITY_SPECS = {
    "customers": {
        "file": "customers.csv",
        "key": "customer_id",
        "required": ["customer_id", "email", "signup_ts", "updated_at", "country_code"],
        "optional": ["full_name", "phone", "ssn", "dob"],
    },
    "orders": {
        "file": "orders.csv",
        "key": "order_id",
        "required": ["order_id", "customer_id", "order_ts", "amount", "currency", "status"],
        "optional": [],
    },
}

# customers first: orders have a foreign key to customers
PROCESSING_ORDER = ["customers", "orders"]


def all_columns(entity: str) -> list:
    spec = ENTITY_SPECS[entity]
    return spec["required"] + spec["optional"]