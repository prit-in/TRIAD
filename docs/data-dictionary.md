# Data dictionary

For everyone: what goes in, what is derived from it, and what the model is actually predicting.

## The label

| | |
| --- | --- |
| **Name** | `churned_90d` |
| **Definition** | 1 when an eligible customer places no completed order in `[T, T + 90 days)` |
| **Eligible population** | Customers with at least one completed order in the 365 days before `T` |
| **Maturity rule** | A snapshot is labelled only when `T + horizon` falls inside the data |
| **Grain** | `customer_id`, `snapshot_date` |

Three things about this definition matter more than the definition itself.

**It is a definition, not a fact.** "Churn" for a retailer with no subscription is a modelling
choice: ninety days of silence is the choice made here. Changing it is a governed action
(`churn_definition.change`, L2 supervised, requiring an ML engineer *and* a business analyst)
because every historical number moves when it changes.

**The maturity rule is what stops the last three months being labelled as churn.** A customer
whose horizon has not finished has not failed to buy; the data simply has not caught up. Without
this rule the most recent cutoffs would all look like a churn spike.

**Eligibility is part of the label.** A customer with no order in the prior year is not counted
as churned, because they were not active to begin with.

## Source tables

Only `transactions` is required. A missing optional source disables the features that depend on
it and marks them unavailable — it never fakes them.

| Table | Required | Grain | What it contributes |
| --- | --- | --- | --- |
| `transactions` | **yes** | `order_id`, `line_id` | Recency, frequency, monetary, basket behaviour, and the label |
| `customers` | no | `customer_id` | Tenure, acquisition channel, country, loyalty tier |
| `products` | no | `product_id` | Category breadth |
| `returns` | no | `return_id` | Return counts and rates |
| `web_sessions` | no | `session_id` | Sessions, product views, cart behaviour |
| `support_tickets` | no | `ticket_id` | Ticket counts, complaints, resolution time, CSAT |
| `promotions` | no | `promo_code` | Discount dependence |
| `customer_snapshots` | no | `customer_id`, `as_of` | Attributes as they were at the cutoff |

### The `transactions` contract

The contract is the thing a stage is checked against, and a violation is a `CONTRACT_VIOLATION`
rather than a surprise later:

| Column | Type | Required | Nullable | Note |
| --- | --- | --- | --- | --- |
| `order_id` | string | yes | no | |
| `line_id` | int | yes | no | |
| `customer_id` | string | yes | **yes** | A null is quarantined as `NO_CUSTOMER_KEY`, never dropped |
| `order_date` | timestamp | yes | no | |
| `product_id` | string | no | yes | |
| `quantity` | int | yes | no | |
| `price` | decimal(18,2) | yes | no | |
| `discount` | decimal(18,2) | no | yes | |

That third row is the one worth reading twice. `customer_id` is *required to exist* as a column
and *allowed to be null* in a row, and the two are different statements. A line with no customer
cannot be joined to a customer, so it is set aside with a reason rather than silently dropped —
which is what makes row accounting (`input = output + quarantined + removed_duplicates`) hold.

## Features

42 features in six groups, defined in `pipelines/feature_catalog/churn_features.yaml`. That file
is the single source of truth for both languages: a generator turns it into Scala case classes
and a Python validation schema, so the two sides cannot drift.

| Group | Count | Examples |
| --- | --- | --- |
| `rfm` | 7 | `recency_days`, `frequency_30d/90d/180d/365d`, `monetary_90d/365d` |
| `purchase_behaviour` | 10 | `aov_365d`, `items_per_order_365d`, `clv_proxy_lifetime`, `return_rate_365d` |
| `customer_service` | 6 | `tickets_90d`, `complaint_tickets_90d`, `avg_resolution_hours_365d`, `avg_csat_365d` |
| `engagement` | 8 | `sessions_30d/90d`, `days_since_last_session`, `product_views_90d`, `cart_conversion_90d` |
| `temporal` | 7 | `tenure_days`, `inter_purchase_mean_days_365d`, `overdue_ratio`, `trend_90_vs_prior_90` |
| `profile` | 4 | `acquisition_channel`, `country`, `loyalty_tier_at_cutoff`, `marketing_opt_in_at_cutoff` |

Every entry carries its definition as of cutoff `T`, its source tables, a computation pattern, a
window, an owner and a **leakage tag**:

| Leakage tag | Meaning |
| --- | --- |
| `point_in_time` | Computed only from events strictly before `T` |
| `point_in_time_strict` | As above, and checked by the sentinel test |
| `point_in_time_attribute` | An attribute as it was at `T`, from a validity-range join |
| `known_at_cutoff` | Knowable at `T` without looking at any event |
| `static_attribute` | Does not change over time |
| `derived` | A ratio or difference of other features, inheriting their tags |

**TC-026 is the test that makes these tags mean something**: events dated on or after the cutoff
must change no feature value. And the sentinel in EXP-9 is what catches what the tags cannot — a
feature named `customer_lifetime_orders` is a name no static rule objects to, and only the
behavioural checks caught it.

### The three attributes that are joined but never trained on

`country`, `loyalty_tier` and `acquisition_channel` are joined at the Spark-to-Python hand-off
**for evaluation only**, and recorded on the split as `evaluation_only_cols`. They are what the
per-segment recall checks (AP-6) group by.

They are kept out of the feature list on purpose. A segment check asks whether a model built from
*behaviour* treats groups differently; a model handed `country` as an input is answering a
different question. This is not a hypothetical concern — the first version of the AP-6 fix joined
them without excluding them, three separate call sites derived their own feature list from the
training frame, and every family's PR-AUC moved.

## Splits

Temporal, never random, with a gap rule of one full label horizon between groups:

```
train cutoffs ... | 90 days | validation cutoff | 90 days | test cutoff
```

The gap exists because a training label is decided by purchases in `[T, T+90)`. Without it,
those same purchases fall inside the validation window and the model is scored on data it has
already seen the consequences of. `check_gap_rule` raises rather than warns, and
`assert_no_overlap` refuses a snapshot date that appears in two parts.

A slice of the *training* cutoffs is held out for calibration, so calibration never sees
validation. Fewer than 200 positive training examples is escalated as `INSUFFICIENT_DATA`
rather than modelled.

## Quality dimensions

The profiling stage scores each version on four dimensions and combines them into the quality
score the gate compares against `min_quality_score` (0.70):

| Dimension | What it measures |
| --- | --- |
| `completeness` | Share of values present where the contract expects them |
| `validity` | Share of values that parse to their declared type |
| `uniqueness` | Share of rows unique on the declared grain |
| `consistency` | Agreement between related columns and across versions |

An issue is `blocking`, `warning` or `info`, and the **contract decides which** — a column
nullable by design, such as `promo_code`, raised a blocking issue until it did.
