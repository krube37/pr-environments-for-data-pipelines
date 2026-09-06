select
    c.segment,
    count(distinct o.order_id) as order_count,
    sum(o.total_amount) as total_amount
from {{ ref('stg_orders') }} o
join {{ ref('stg_customers') }} c using (customer_id)
group by c.segment
