import unittest
from unittest import mock

from sensitive_pipeline import storage
from sensitive_pipeline.pipeline import run_pipeline
from sensitive_pipeline.reconcile import run_checks
from tests.helpers import CUST_HDR, ORD_HDR, PipelineTestCase, cust, order, write_csv


class HappyPathAndBasics(PipelineTestCase):
    def test_happy_path_loads_protects_and_publishes(self):
        self.put([cust(1), cust(2)], [order(1, 1), order(2, 2, amount="25.50")])
        s = run_pipeline(self.cfg, run_id="t1")
        self.assertEqual(s["status"], "SUCCESS")
        self.assertEqual(self.entity(s, "customers")["inserted"], 2)
        self.assertEqual(self.entity(s, "orders")["inserted"], 2)
        # silver is protected
        email_p, phone_p, ssn_p, dob_p, name_p = self.q(
            "SELECT email_p,phone_p,ssn_p,dob_p,full_name_p FROM silver_customers WHERE customer_id='C0000001'")[0]
        self.assertTrue(email_p.startswith("tok_"))
        self.assertEqual(phone_p, "*******0123")
        self.assertEqual(ssn_p, "***-**-6789")
        self.assertEqual(dob_p, "1990")
        self.assertIsNone(name_p)
        # gold is minimized
        cols = [r[1] for r in self.q("PRAGMA table_info(gold_dim_customer)")]
        self.assertNotIn("phone", cols)
        self.assertNotIn("ssn", cols)
        self.assertEqual(self.q("SELECT SUM(amount_cents) FROM gold_fact_order")[0][0], 3550)
        self.assertTrue((self.cfg.output_dir / "published" / "gold_dim_customer.csv").exists())
        self.assertTrue(all(c["result"] == "PASS" for c in run_checks(self.db())))

    def test_empty_batch_header_only(self):
        self.put([], [])
        s = run_pipeline(self.cfg, run_id="t_empty")
        self.assertEqual(s["status"], "SUCCESS")
        self.assertEqual(s["totals"]["input_rows"], 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM silver_customers")[0][0], 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM audit_log WHERE action='empty_batch'")[0][0], 2)

    def test_empty_batch_zero_byte_file(self):
        (self.tmp / "in" / "customers.csv").write_text("")
        write_csv(self.tmp / "in" / "orders.csv", ORD_HDR, [])
        s = run_pipeline(self.cfg, run_id="t_zero")
        self.assertEqual(s["status"], "SUCCESS")

    def test_missing_input_file_is_a_recorded_failure(self):
        s = run_pipeline(self.cfg, run_id="t_nofile")
        self.assertEqual(s["status"], "FAILED")
        self.assertIn("input_file_missing", s["failure_reason"])


class MalformedNullsAndRules(PipelineTestCase):
    def test_malformed_row_wrong_field_count_is_quarantined(self):
        self.put([cust(1)], [order(1, 1)])
        with open(self.tmp / "in" / "customers.csv", "a", newline="") as fh:
            fh.write("C0000002,only,three\n")
        s = run_pipeline(self.cfg, run_id="t_bad")
        self.assertEqual(self.entity(s, "customers")["rejected"], 1)
        row = self.q("SELECT reject_reason, source_line_number FROM quarantine_records")[0]
        self.assertEqual(row, ("malformed_row", 3))

    def test_missing_required_field_and_nulls(self):
        self.put([cust(1, email=""), cust(2, phone="", ssn="", dob="")], [])
        s = run_pipeline(self.cfg, run_id="t_null")
        self.assertEqual(self.entity(s, "customers")["rejected"], 1)   # missing required email
        self.assertEqual(self.entity(s, "customers")["inserted"], 1)   # optional nulls are fine
        self.assertEqual(self.q("SELECT reject_reason FROM quarantine_records")[0][0], "missing_required_field:email")
        self.assertIsNone(self.q("SELECT phone_p FROM silver_customers")[0][0])

    def test_quarantine_never_stores_raw_pii(self):
        self.put([cust(1, email="bad-email", ssn="123-45-6789")], [])
        run_pipeline(self.cfg, run_id="t_pii")
        raw = self.q("SELECT raw_record_redacted FROM quarantine_records")[0][0]
        self.assertNotIn("123-45-6789", raw)
        self.assertNotIn("bad-email", raw)
        self.assertIn("[REDACTED]", raw)

    # ---- domain edge cases ----
    def test_amount_boundaries(self):
        self.put([cust(1)], [order(1, 1, amount="0.01"), order(2, 1, amount="1000000.00"),
                             order(3, 1, amount="1000000.01"), order(4, 1, amount="0"),
                             order(5, 1, amount="10.999"), order(6, 1, amount="NaN")])
        s = run_pipeline(self.cfg, run_id="t_amt")
        self.assertEqual(self.entity(s, "orders")["inserted"], 2)
        reasons = sorted(r[0] for r in self.q("SELECT reject_reason FROM quarantine_records"))
        self.assertEqual(reasons, ["invalid_amount", "invalid_amount_non_positive",
                                   "invalid_amount_precision", "invalid_amount_too_large"])

    def test_dob_future_and_updated_before_signup(self):
        self.put([cust(1, dob="2999-01-01"), cust(2, updated_at="2023-12-31T00:00:00Z"),
                  cust(3, signup_ts="2024-01-01T10:00:00+05:30", updated_at="2024-01-01T06:00:00Z")], [])
        s = run_pipeline(self.cfg, run_id="t_dom")
        reasons = {r[0] for r in self.q("SELECT reject_reason FROM quarantine_records")}
        self.assertEqual(reasons, {"invalid_dob", "updated_before_signup"})
        self.assertEqual(self.entity(s, "customers")["inserted"], 1)  # tz-aware timestamps normalised to UTC

    def test_invalid_reference_key(self):
        self.put([cust(1)], [order(1, 1), order(2, 99)])
        s = run_pipeline(self.cfg, run_id="t_fk")
        self.assertEqual(self.entity(s, "orders")["rejected"], 1)
        self.assertEqual(self.q("SELECT reject_reason FROM quarantine_records")[0][0], "invalid_reference_key")


class DuplicatesHistoryAndLateData(PipelineTestCase):
    def test_exact_and_conflicting_duplicates(self):
        self.put([cust(1), cust(1)], [order(1, 1), order(1, 1), order(1, 1, amount="99.00")])
        s = run_pipeline(self.cfg, run_id="t_dup")
        self.assertEqual(self.entity(s, "customers")["rejected"], 1)
        self.assertEqual(self.entity(s, "orders")["inserted"], 1)
        reasons = sorted(r[0] for r in self.q("SELECT reject_reason FROM quarantine_records WHERE entity='orders'"))
        self.assertEqual(reasons, ["conflicting_duplicate_key", "duplicate_record"])

    def test_out_of_order_update_in_same_file_keeps_history(self):
        newer = cust(1, phone="+14155559999", updated_at="2024-03-01T00:00:00Z")
        self.put([newer, cust(1)], [])             # newer version appears first in the file
        s = run_pipeline(self.cfg, run_id="t_ooo")
        self.assertEqual(self.entity(s, "customers")["inserted"], 1)
        self.assertEqual(self.entity(s, "customers")["updated"], 1)
        self.assertEqual(self.q("SELECT phone_p, updated_at FROM silver_customers")[0],
                         ("*******9999", "2024-03-01T00:00:00Z"))
        self.assertEqual(self.q("SELECT phone_p FROM silver_customers_history")[0][0], "*******0123")

    def test_late_arriving_older_version_does_not_overwrite(self):
        self.put([cust(1, phone="+14155559999", updated_at="2024-03-01T00:00:00Z")], [])
        run_pipeline(self.cfg, run_id="t_late1")
        self.put([cust(1)], [])                    # older version arrives in a later file
        s = run_pipeline(self.cfg, run_id="t_late2")
        self.assertEqual(self.entity(s, "customers")["stale_ignored"], 1)
        self.assertEqual(self.q("SELECT phone_p FROM silver_customers")[0][0], "*******9999")
        self.assertEqual(self.q("SELECT reject_reason FROM quarantine_records")[0][0], "late_arriving_stale_update")

    def test_new_file_with_real_change_updates_and_archives(self):
        self.put([cust(1)], [])
        run_pipeline(self.cfg, run_id="t_c1")
        self.put([cust(1, phone="+14155550000", updated_at="2024-05-01T00:00:00Z")], [])
        s = run_pipeline(self.cfg, run_id="t_c2")
        self.assertEqual(self.entity(s, "customers")["updated"], 1)
        self.assertEqual(self.q("SELECT COUNT(*) FROM silver_customers_history")[0][0], 1)


class SchemaChange(PipelineTestCase):
    def test_missing_required_column_fails_that_entity_only(self):
        hdr = [c for c in CUST_HDR if c != "email"]
        self.put([cust(1)], [], c_header=hdr)
        s = run_pipeline(self.cfg, run_id="t_schema")
        self.assertEqual(self.entity(s, "customers")["status"], "FAILED")
        self.assertIn("missing_required_columns:email", self.entity(s, "customers")["failure_reason"])
        self.assertEqual(self.entity(s, "orders")["status"], "SUCCESS")
        self.assertEqual(s["status"], "PARTIAL_FAILURE")
        self.assertEqual(self.q("SELECT COUNT(*) FROM silver_customers")[0][0], 0)

    def test_unexpected_extra_column_is_ignored_and_audited(self):
        self.put([dict(cust(1), home_address="1 Secret Street")], [], c_header=CUST_HDR + ["home_address"])
        s = run_pipeline(self.cfg, run_id="t_extra")
        self.assertEqual(self.entity(s, "customers")["inserted"], 1)
        self.assertEqual(self.q("SELECT COUNT(*) FROM audit_log WHERE action='schema_drift_extra_columns_ignored'")[0][0], 1)
        dump = " ".join(str(r) for r in self.q("SELECT * FROM silver_customers"))
        self.assertNotIn("Secret", dump)       # unknown columns are never carried forward


class IdempotencyAndRecovery(PipelineTestCase):
    def test_rerun_same_input_is_skipped(self):
        self.put([cust(1)], [order(1, 1)])
        run_pipeline(self.cfg, run_id="r1")
        s = run_pipeline(self.cfg, run_id="r2")
        self.assertEqual(self.entity(s, "customers")["status"], "SKIPPED_ALREADY_PROCESSED")
        self.assertEqual(self.q("SELECT COUNT(*) FROM silver_orders")[0][0], 1)

    def test_forced_rerun_creates_no_duplicates(self):
        self.put([cust(1), cust(1), cust(2, email="bad")], [order(1, 1)])
        run_pipeline(self.cfg, run_id="f1")
        snap = lambda: (self.q("SELECT * FROM silver_customers ORDER BY 1"),  # noqa: E731
                        self.q("SELECT COUNT(*) FROM quarantine_records")[0][0],
                        self.q("SELECT COUNT(*) FROM silver_customers_history")[0][0],
                        self.q("SELECT COUNT(*) FROM gold_fact_order")[0][0])
        before = snap()
        s = run_pipeline(self.cfg, run_id="f2", force=True)
        after = snap()
        self.assertEqual(self.entity(s, "customers")["unchanged"], 1)
        self.assertEqual(before[1:], after[1:])
        self.assertEqual([r[:10] for r in before[0]], [r[:10] for r in after[0]])

    def test_partial_failure_rolls_back_then_retry_succeeds(self):
        self.put([cust(1)], [order(1, 1), order(2, 1), order(3, 1)])
        real = storage.insert_order
        calls = {"n": 0}

        def flaky(conn, rec):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("simulated crash mid-load")
            return real(conn, rec)

        with mock.patch.object(storage, "insert_order", flaky):
            s = run_pipeline(self.cfg, run_id="p1")
        self.assertEqual(s["status"], "PARTIAL_FAILURE")
        self.assertIn("simulated crash", self.entity(s, "orders")["failure_reason"])
        self.assertEqual(self.q("SELECT COUNT(*) FROM silver_orders")[0][0], 0)      # rolled back, nothing half-loaded
        self.assertEqual(self.q("SELECT status FROM pipeline_run WHERE run_id='p1'")[0][0], "PARTIAL_FAILURE")
        s2 = run_pipeline(self.cfg, run_id="p2")                                      # retry / restart
        self.assertEqual(s2["status"], "SUCCESS")
        self.assertEqual(self.entity(s2, "customers")["status"], "SKIPPED_ALREADY_PROCESSED")
        self.assertEqual(self.entity(s2, "orders")["inserted"], 3)

    def test_backfill_from_other_directory_does_not_clobber_newer_data(self):
        self.put([cust(1, phone="+14155559999", updated_at="2024-06-01T00:00:00Z")], [])
        run_pipeline(self.cfg, run_id="b1")
        backfill = self.tmp / "history"
        backfill.mkdir()
        write_csv(backfill / "customers.csv", CUST_HDR, [cust(1), cust(2)])
        write_csv(backfill / "orders.csv", ORD_HDR, [])
        from sensitive_pipeline.config import load_config
        cfg2 = load_config("config/pipeline_config.json", input_dir=backfill, output_dir=self.tmp / "out")
        s = run_pipeline(cfg2, run_id="b2")
        self.assertEqual(self.entity(s, "customers")["stale_ignored"], 1)
        self.assertEqual(self.entity(s, "customers")["inserted"], 1)
        self.assertEqual(self.q("SELECT phone_p FROM silver_customers WHERE customer_id='C0000001'")[0][0], "*******9999")


if __name__ == "__main__":
    unittest.main()