---
name: sales-trends
description: Explain a sales comparison when the request asks about trends, growth, decline or change over time.
---

Call `shopify_sales_comparison(days=N)`, where N is the inclusive number of calendar dates from the report's `period_start` to `period_end`. If that tool is not granted or fails, state that the comparison is unavailable; do not reconstruct it with other queries.

Use its calculated changes, category ordering and high-sales days directly. Both periods contain N dates, including zero-sales days. `change_without_high_sales_days` excludes identified high-sales days from both periods. A null percentage means the previous value is zero; say "new" only when `new` is true. If `meets_minimum_order_count` is false, state that the sample is too small and make no trend claim.

Reply in plain text, no heading, at most 120 words: net merchandise sales and order counts for both periods with currency; category changes in supplied order; material high-sales days and the supplied change without high-sales days. Do not calculate additional statistics or infer causes from sales figures alone.
