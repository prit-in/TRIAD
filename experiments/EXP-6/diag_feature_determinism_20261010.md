# Why EXP-6's feature table differed under memory pressure, 10-10-2026

EXP-6 (`20261010T113128Z`) built features from the same 20 000 customers at the configured memory
and at 460m: the cleaned tables were identical, the feature tables were not (same 110 900 rows,
different content hash). Both arms had committed to one folder, so a diagnostic
(`experiments/_logs/diag_feature_determinism.py`, not committed: `_logs` is ignored) rebuilt both,
into separate folders, from EXP-6's own memory-arm data, and compared them column by column.
Its report, as printed:

```
REPORT {"rows": [110900, 110900], "same_keys": true, "columns": {"monetary_90d": {"kind": "numeric", "rows_differing": 3, "max_abs_diff": 7.275957614183426e-12, "max_rel_diff": 8.188575131439919e-17}, "monetary_365d": {"kind": "numeric", "rows_differing": 9, "max_abs_diff": 1.4551915228366852e-11, "max_rel_diff": 4.908979503631829e-17}, "aov_365d": {"kind": "numeric", "rows_differing": 9, "max_abs_diff": 1.3642420526593924e-12, "max_rel_diff": 8.517128018685613e-17}}}
```

Every key and every non-monetary feature is identical. Three monetary features differ in at most 9
of 110 900 rows, by less than 1e-16 relative - the last bit of a double. Under memory pressure
Spark merged spilled partial sums in a different order, and floating-point addition is not
associative; the content hash compares exact values (the multiset of rows), so it differs.

So a feature table is reproducible bit for bit only under the same physical plan - which is what
ATC-18's replay checks - not across memory settings. Summing money as `decimal`, as the contract
declares it, would make the sums exact and order-independent; that change is to the feature
template, and is left as stated future work rather than made in the middle of the experiment
campaign, where it would invalidate every cached gate-5 result.
