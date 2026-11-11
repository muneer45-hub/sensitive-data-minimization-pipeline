
# Data Dictionary

## 1. Input Tables

### customers.csv

| Column | Description |
|---|---|
| customer_id | Unique customer identifier |
| full_name | Customer full name |
| email | Customer email address |
| phone | Customer phone number |
| ssn | Social security number |
| dob | Date of birth |
| country_code | Customer country |
| signup_ts | Customer signup timestamp |
| updated_at | Last updated timestamp |

### orders.csv

| Column | Description |
|---|---|
| order_id | Unique order identifier |
| customer_id | Related customer identifier |
| order_ts | Order timestamp |
| amount | Order amount |
| currency | Transaction currency |
| status | Order status |

## 2. Silver Tables

- silver_customers
- silver_customers_history
- silver_orders
- quarantine_records

These tables contain validated and protected records.

## 3. Gold Tables

- gold_dim_customer
- gold_fact_order
- gold_daily_sales
- gold_sales_by_country

Gold contains minimized analytical data.

Direct identifiers and sensitive information
such as full names, phone numbers, SSNs,
exact dates of birth and raw emails are removed
or protected according to the configured policies.

## 4. Control Tables

- pipeline_run
- entity_run_metrics
- audit_log
- bronze_file_manifest