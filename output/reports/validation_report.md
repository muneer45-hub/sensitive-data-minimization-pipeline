# Validation & reconciliation report

## Reconciliation checks

| check | result | detail |
|---|---|---|
| [customers] latest run processed | **PASS** | status=SKIPPED_ALREADY_PROCESSED (no counts to reconcile) |
| [orders] latest run processed | **PASS** | status=SKIPPED_ALREADY_PROCESSED (no counts to reconcile) |
| silver customers = gold customers | **PASS** | 194 vs 194 |
| silver orders = gold orders | **PASS** | 579 vs 579 |
| sum(amount_cents) silver = gold | **PASS** | 325886772 vs 325886772 |
| referential integrity: no orphan orders in silver | **PASS** | orphans=0 |
| referential integrity: no orphan orders in gold | **PASS** | orphans=0 |
| primary keys unique in gold | **PASS** | dup customers=0, dup orders=0 |
| no raw email in gold / raw SSN or full DOB in silver | **PASS** | email=0, ssn=0, full_dob=0 |
| gold customer table is minimized (no direct identifiers) | **PASS** | columns=['customer_id', 'email_token', 'birth_year', 'country_code', 'signup_date', 'last_updated_at'] |

## Labelled-defect evaluation (labels kept separate from input)

- expected defects: 47
- correctly quarantined with the expected reason: 47
- missed: none
- unexpected quarantine rows (false positives): none

## Sample analytical queries (run against the minimized gold layer)

### Top 5 days by revenue (non-cancelled, USD)

```sql
SELECT order_date, order_count, total_amount FROM gold_daily_sales WHERE currency='USD' ORDER BY total_amount DESC LIMIT 5
```

| order_date | order_count | total_amount |
|---|---|---|
| 2025-04-08 | 2 | 1597.48 |
| 2023-01-26 | 2 | 1424.67 |
| 2023-01-19 | 2 | 1320.17 |
| 2025-04-30 | 1 | 893.93 |
| 2024-06-09 | 1 | 880.22 |

### Revenue by country (USD)

```sql
SELECT country_code, order_count, total_amount FROM gold_sales_by_country WHERE currency='USD' ORDER BY total_amount DESC
```

| country_code | order_count | total_amount |
|---|---|---|
| US | 20 | 10046.75 |
| AU | 19 | 9918.13 |
| FR | 19 | 7490.9 |
| CA | 18 | 7336.94 |
| GB | 12 | 7169.59 |
| SG | 12 | 4972.22 |
| DE | 11 | 4226.18 |
| IN | 9 | 1726.29 |

### Customers by birth-decade (generalized, no exact DOB available)

```sql
SELECT (birth_year/10)*10 AS birth_decade, COUNT(*) AS customers FROM gold_dim_customer WHERE birth_year IS NOT NULL GROUP BY 1 ORDER BY 1
```

| birth_decade | customers |
|---|---|
| 1950 | 38 |
| 1960 | 42 |
| 1970 | 28 |
| 1980 | 32 |
| 1990 | 33 |
| 2000 | 21 |

### Quarantine summary (why records were rejected)

```sql
SELECT entity, reject_reason, COUNT(*) AS records FROM quarantine_records GROUP BY entity, reject_reason ORDER BY records DESC
```

| entity | reject_reason | records |
|---|---|---|
| customers | duplicate_record | 10 |
| orders | invalid_timestamp:order_ts | 7 |
| orders | conflicting_duplicate_key | 5 |
| orders | duplicate_record | 5 |
| orders | invalid_status | 4 |
| orders | invalid_reference_key | 3 |
| customers | missing_required_field:customer_id | 2 |
| orders | invalid_amount | 2 |
| orders | invalid_currency | 2 |
| orders | missing_required_field:order_id | 2 |
| customers | invalid_email | 1 |
| customers | invalid_ssn | 1 |
| customers | invalid_timestamp:signup_ts | 1 |
| customers | missing_required_field:email | 1 |
| orders | invalid_amount_non_positive | 1 |

### Customers with change history (SCD-style)

```sql
SELECT COUNT(DISTINCT customer_id) AS customers_with_history, COUNT(*) AS history_rows FROM silver_customers_history
```

| customers_with_history | history_rows |
|---|---|
| 8 | 8 |
