select
    customer_id,
    name,
    email,
    segment,
    signup_date
from {{ source('prod', 'customers') }}
