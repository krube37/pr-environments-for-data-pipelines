select
    order_id,
    cast(null as string) as customer_id,
    order_date,
    safe_cast(total_amount as numeric) as total_amount,
    currency,
    status
from {{ source('prod', 'orders') }}
