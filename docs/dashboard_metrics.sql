-- 1. Выручка по месяцам за все время.
WITH monthly_revenue AS (
    SELECT date_trunc('month', period_start)::date AS month,
           SUM(revenue) AS revenue
    FROM portfolio_clientperiodfact
    GROUP BY 1
    UNION ALL
    SELECT date_trunc('month', order_date)::date AS month,
           SUM(amount) AS revenue
    FROM portfolio_order
    WHERE status <> 'cancelled'
    GROUP BY 1
)
SELECT month, SUM(revenue) AS revenue
FROM monthly_revenue
GROUP BY month
ORDER BY month;

-- 2. Количество активных клиентов по месяцам.
WITH active_clients AS (
    SELECT date_trunc('month', period_start)::date AS month,
           client_id
    FROM portfolio_clientperiodfact
    WHERE revenue > 0
    UNION
    SELECT date_trunc('month', order_date)::date AS month,
           client_id
    FROM portfolio_order
    WHERE amount > 0 AND status <> 'cancelled'
)
SELECT month, COUNT(DISTINCT client_id) AS active_clients
FROM active_clients
GROUP BY month
ORDER BY month;

-- 3. Средний чек по месяцам.
WITH monthly_orders AS (
    SELECT date_trunc('month', period_start)::date AS month,
           SUM(revenue) AS revenue,
           SUM(GREATEST(orders_count, 1)) AS orders_count
    FROM portfolio_clientperiodfact
    WHERE revenue > 0
    GROUP BY 1
    UNION ALL
    SELECT date_trunc('month', order_date)::date AS month,
           SUM(amount) AS revenue,
           COUNT(*) AS orders_count
    FROM portfolio_order
    WHERE amount > 0 AND status <> 'cancelled'
    GROUP BY 1
)
SELECT month,
       SUM(revenue) / NULLIF(SUM(orders_count), 0) AS average_check
FROM monthly_orders
GROUP BY month
ORDER BY month;

-- 4. Прирост/падение выручки месяц к месяцу.
WITH monthly_revenue AS (
    SELECT month, SUM(revenue) AS revenue
    FROM (
        SELECT date_trunc('month', period_start)::date AS month, SUM(revenue) AS revenue
        FROM portfolio_clientperiodfact
        GROUP BY 1
        UNION ALL
        SELECT date_trunc('month', order_date)::date AS month, SUM(amount) AS revenue
        FROM portfolio_order
        WHERE status <> 'cancelled'
        GROUP BY 1
    ) source
    GROUP BY month
),
with_previous AS (
    SELECT month,
           revenue,
           LAG(revenue) OVER (ORDER BY month) AS previous_revenue
    FROM monthly_revenue
)
SELECT month,
       revenue,
       CASE
           WHEN previous_revenue IS NULL OR previous_revenue = 0 THEN 0
           ELSE ROUND(((revenue - previous_revenue) / previous_revenue) * 100, 2)
       END AS growth_rate_percent
FROM with_previous
ORDER BY month;
