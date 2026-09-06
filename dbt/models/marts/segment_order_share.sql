-- Deliberately reads only segment/order_count from the upstream model, not
-- total_amount — so the demo's "drop total_amount" PR breaks the diff check
-- without also hard-failing this model's build.
select
    segment,
    order_count,
    order_count / sum(order_count) over () as order_share
from {{ ref('revenue_by_customer_segment') }}
