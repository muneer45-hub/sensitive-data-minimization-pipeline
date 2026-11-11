
# Sensitive Data Minimization Pipeline

## 1. Purpose

This project builds a data engineering pipeline that processes sensitive customer and order data. It validates records, masks or tokenizes sensitive information, and publishes minimized analytical datasets with audit logs.

## 2. Architecture

The pipeline follows a three-layer architecture:

CSV Input
   |
   v
BRONZE - Raw data storage and file tracking
   |
   v
SILVER - Validation, masking, tokenization,
         deduplication and quarantine
   |
   v
GOLD - Minimized analytical tables and CSV exports

Supporting components:
- Pipeline run logs
- Audit logs
- Data quality metrics
- Reconciliation reports

## 3. How to Run

Run these commands from the project root:

python -m sensitive_pipeline.cli generate --out sample_data

python -m sensitive_pipeline.cli profile

python -m sensitive_pipeline.cli run

python -m sensitive_pipeline.cli report

python -m unittest discover -s tests -t . -v

python -m sensitive_pipeline.cli benchmark

## 4. Naming Conventions

- Python files use snake_case.
- Bronze, Silver and Gold identify the data layers.
- Protected fields use the _p suffix.
- Run identifiers use run_id.
- Source tracking uses source_file and source_line_number.
- Timestamps use ISO-8601 UTC format.

## 5. Business Rules

- Required fields must be present.
- Customer and order identifiers must be valid.
- Invalid records are rejected or quarantined.
- Duplicate records are handled using defined keys.
- Foreign key relationships are validated.
- Sensitive customer information is minimized before Gold publication.

## 6. Edge Cases

The pipeline handles missing fields, invalid formats,
duplicate records, invalid foreign keys, late-arriving
data and out-of-order updates.

## 7. Idempotency and Recovery

The pipeline tracks processed files using SHA-256 checksums.
Already processed files can be skipped.
Transactions help maintain data consistency.

## 8. Observability

The pipeline maintains run history, entity metrics,
audit logs and data quality reports.

## 9. Results

Document the actual results from your successful pipeline runs:
- Customer input and processed rows.
- Order input and processed rows.
- Rejected records.
- Correctly quarantined records.
- Reconciliation results.

## 10. Performance

Refer to output/reports/performance.md for benchmark
measurements and optimization results.

## 11. Limitations and Future Improvements

- Raw sensitive data requires encryption and access controls
  in production.
- Large files are currently loaded into memory.
- Gold tables use a full refresh.
- Production deployment could use cloud storage,
  distributed processing and managed key rotation.