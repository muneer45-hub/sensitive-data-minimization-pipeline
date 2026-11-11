
# Design Tradeoffs

## 1. SQLite

SQLite provides a lightweight database without requiring
an external database server. Its limitation is that it
is designed for local or limited concurrent workloads.

## 2. Three-Layer Architecture

Bronze preserves the received data, Silver validates
and protects it, and Gold publishes minimized data
for analytics.

## 3. Configurable Policies

Masking and tokenization rules are maintained in
the configuration file, making policy changes easier.

## 4. Tokenization

HMAC-based tokenization provides stable pseudonymous
values. The secret key must be protected.

## 5. Quarantine

Invalid records are tracked with source information
so that data quality issues can be investigated.

## 6. Data History

History tables preserve changes instead of
silently overwriting previous records.

## 7. Transaction Management

Processing files within transactions helps prevent
partially committed results.

## 8. Performance Optimization

The pipeline uses optimized transaction handling.
Refer to performance.md for actual benchmark results.

## 9. Limitations

- Production encryption and access controls are needed.
- Large files may require streaming.
- Gold uses a full refresh.
- Key rotation requires careful token regeneration.