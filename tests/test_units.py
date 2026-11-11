import json
import tempfile
import unittest
from pathlib import Path

from sensitive_pipeline.config import load_config
from sensitive_pipeline.generator import generate
from sensitive_pipeline.pipeline import run_pipeline
from sensitive_pipeline.profiler import profile_file
from sensitive_pipeline.protection import Protector, redact_record
from sensitive_pipeline.reconcile import build_report
from sensitive_pipeline import storage
from sensitive_pipeline.validators import normalize_ts


class ProtectionTests(unittest.TestCase):
    def test_tokenization_is_deterministic_and_key_dependent(self):
        a, b = Protector("k1"), Protector("k2")
        self.assertEqual(a.tokenize("Maya@Example.com "), a.tokenize("maya@example.com"))
        self.assertNotEqual(a.tokenize("maya@example.com"), b.tokenize("maya@example.com"))
        self.assertNotIn("maya", a.tokenize("maya@example.com"))

    def test_masks(self):
        p = Protector("k")
        self.assertEqual(p.apply("mask_email", "maya@example.com"), "m***@example.com")
        self.assertEqual(p.apply("mask_phone", "+1 (415) 555-0123"), "*******0123")
        self.assertEqual(p.apply("mask_ssn", "123-45-6789"), "***-**-6789")
        self.assertEqual(p.apply("generalize_dob", "1990-05-17"), "1990")
        self.assertIsNone(p.apply("drop", "x"))
        self.assertIsNone(p.apply("tokenize", ""))
        with self.assertRaises(ValueError):
            p.apply("nope", "x")

    def test_redact_record(self):
        out = redact_record({"customer_id": "C1", "email": "a@b.com", "extra": "x"},
                            ["customer_id", "email"], {"email": "tokenize"})
        self.assertEqual(out, {"customer_id": "C1", "email": "[REDACTED]"})

    def test_normalize_ts(self):
        self.assertEqual(normalize_ts("2024-01-01T10:00:00+02:00"), "2024-01-01T08:00:00Z")
        self.assertIsNone(normalize_ts("2024-01-01"))
        self.assertIsNone(normalize_ts("garbage"))


class ConfigTests(unittest.TestCase):
    def test_invalid_policy_action_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            raw = json.loads(Path("config/pipeline_config.json").read_text())
            raw["policies"]["customers"]["email"] = "rot13"
            p = Path(d) / "c.json"
            p.write_text(json.dumps(raw))
            with self.assertRaises(ValueError):
                load_config(p)


class GeneratorAndEndToEnd(unittest.TestCase):
    def test_generator_is_reproducible(self):
        with tempfile.TemporaryDirectory() as d:
            generate(Path(d) / "a", 100, 300, seed=5)
            generate(Path(d) / "b", 100, 300, seed=5)
            generate(Path(d) / "c", 100, 300, seed=6)
            read = lambda x: (Path(d) / x / "input" / "customers.csv").read_bytes()  # noqa: E731
            self.assertEqual(read("a"), read("b"))
            self.assertNotEqual(read("a"), read("c"))

    def test_end_to_end_every_labelled_defect_is_caught_and_reconciles(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            generate(d / "data", 500, 1500, seed=11)
            cfg = load_config("config/pipeline_config.json", input_dir=d / "data" / "input", output_dir=d / "out")
            s = run_pipeline(cfg, run_id="e2e")
            self.assertEqual(s["status"], "SUCCESS")
            conn = storage.connect(cfg.db_path)
            result = build_report(conn, d / "data" / "labels" / "expected_labels.csv")
            conn.close()
            self.assertTrue(result["labels"]["ok"], result["labels"])
            self.assertTrue(result["all_ok"], [c for c in result["checks"] if c["result"] != "PASS"])

    def test_profiler_flags_unprotected_sensitive_columns(self):
        with tempfile.TemporaryDirectory() as d:
            generate(Path(d), 50, 10, seed=1)
            path = Path(d) / "input" / "customers.csv"
            full = profile_file(path, "customers", load_config("config/pipeline_config.json").policies["customers"])
            self.assertEqual(full["warnings"], [])
            none = profile_file(path, "customers", {})
            self.assertEqual(set(none["warnings"]), {"full_name", "email", "phone", "ssn", "dob"})


if __name__ == "__main__":
    unittest.main()