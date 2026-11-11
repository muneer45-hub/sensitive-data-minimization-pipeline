# Sensitive field profile

## customers (customers.csv, 218 rows)

| column | null_rate | distinct | detected_pii | sensitivity | action | status |
|---|---|---|---|---|---|---|
| customer_id | 0.0092 | 199 | None | none | None | not_sensitive |
| full_name | 0.0 | 100 | person_name | medium | drop | PROTECTED |
| email | 0.0046 | 200 | email | medium | tokenize | PROTECTED |
| phone | 0.078 | 194 | phone | medium | mask_phone | PROTECTED |
| ssn | 0.0 | 200 | national_id | high | mask_ssn | PROTECTED |
| dob | 0.0 | 200 | date_of_birth | high | generalize_dob | PROTECTED |
| country_code | 0.0 | 8 | None | none | None | not_sensitive |
| signup_ts | 0.0 | 200 | None | none | None | not_sensitive |
| updated_at | 0.0 | 208 | None | none | None | not_sensitive |

Unprotected sensitive columns: none

## orders (orders.csv, 610 rows)

| column | null_rate | distinct | detected_pii | sensitivity | action | status |
|---|---|---|---|---|---|---|
| order_id | 0.0033 | 599 | None | none | None | not_sensitive |
| customer_id | 0.0 | 186 | None | none | None | not_sensitive |
| order_ts | 0.0 | 594 | None | none | None | not_sensitive |
| amount | 0.0 | 595 | None | none | None | not_sensitive |
| currency | 0.0 | 5 | None | none | None | not_sensitive |
| status | 0.0 | 5 | None | none | None | not_sensitive |

Unprotected sensitive columns: none
