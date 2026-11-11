
import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sensitive_pipeline.config import load_config

CUST_HDR = [
    "customer_id", "full_name", "email", "phone", "ssn",
    "dob", "country_code", "signup_ts", "updated_at"
]

ORD_HDR = [
    "order_id", "customer_id", "order_ts",
    "amount", "currency", "status"
]


def cust(i=1, **kw):
    row = {
        "customer_id": f"C{i:07d}",
        "full_name": "Maya Rao",
        "email": f"maya{i}@example.com",
        "phone": "+14155550123",
        "ssn": "123-45-6789",
        "dob": "1990-05-17",
        "country_code": "US",
        "signup_ts": "2024-01-01T10:00:00Z",
        "updated_at": "2024-01-02T10:00:00Z"
    }
    row.update(kw)
    return row


def order(i=1, cid=1, **kw):
    row = {
        "order_id": f"O{i:09d}",
        "customer_id": f"C{cid:07d}",
        "order_ts": "2024-02-01T09:00:00Z",
        "amount": "10.00",
        "currency": "USD",
        "status": "PLACED"
    }
    row.update(kw)
    return row


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=header,
            extrasaction="ignore",
            lineterminator="\n"
        )
        w.writeheader()
        w.writerows(rows)


class PipelineTestCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

        self._connections = []

        (self.tmp / "in").mkdir()

        self.cfg = load_config(
            "config/pipeline_config.json",
            input_dir=self.tmp / "in",
            output_dir=self.tmp / "out"
        )

    def tearDown(self):
        for conn in self._connections:
            try:
                conn.close()
            except sqlite3.Error:
                pass

        self._connections.clear()
        self._tmp.cleanup()

    def put(
        self,
        customers=None,
        orders=None,
        c_header=CUST_HDR,
        o_header=ORD_HDR
    ):
        write_csv(
            self.tmp / "in" / "customers.csv",
            c_header,
            customers if customers is not None else []
        )

        write_csv(
            self.tmp / "in" / "orders.csv",
            o_header,
            orders if orders is not None else []
        )

    def db(self):
        conn = sqlite3.connect(str(self.cfg.db_path))
        self._connections.append(conn)
        return conn

    def q(self, sql, params=()):
        conn = self.db()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def entity(self, summary, name):
        return next(
            e for e in summary["entities"]
            if e["entity"] == name
        )
