-- Общие итоги менеджера за исторический период и годовые KPI.
-- :manager_id и :today передаются из приложения.
WITH manager_clients AS (
    SELECT DISTINCT client_id
    FROM portfolio_clientmanagerassignment
    WHERE manager_id = :manager_id
      AND valid_from <= :today
      AND (valid_to IS NULL OR valid_to >= :today)
),
order_totals AS (
    SELECT
        SUM(o.amount) AS total_revenue,
        SUM(o.amount - o.cost_amount) AS total_profit,
        COUNT(*) AS orders_count,
        COUNT(DISTINCT o.client_id) AS bought_clients,
        SUM(o.amount) FILTER (
            WHERE o.order_date BETWEEN date_trunc('year', :today::date)::date AND :today::date
        ) AS current_year_revenue,
        SUM(o.amount) FILTER (
            WHERE o.order_date BETWEEN (date_trunc('year', :today::date) - interval '1 year')::date
                                  AND (date_trunc('year', :today::date) - interval '1 day')::date
        ) AS previous_year_revenue
    FROM portfolio_order o
    WHERE o.client_id IN (SELECT client_id FROM manager_clients)
      AND o.status = 'completed'
),
fact_totals AS (
    SELECT
        SUM(f.revenue) AS total_revenue,
        SUM(f.revenue) AS total_profit,
        SUM(f.orders_count) AS orders_count,
        COUNT(DISTINCT f.client_id) AS bought_clients,
        SUM(f.revenue) FILTER (
            WHERE f.period_start >= date_trunc('year', :today::date)::date
              AND f.period_end <= :today::date
        ) AS current_year_revenue,
        SUM(f.revenue) FILTER (
            WHERE f.period_start >= (date_trunc('year', :today::date) - interval '1 year')::date
              AND f.period_end <= (date_trunc('year', :today::date) - interval '1 day')::date
        ) AS previous_year_revenue
    FROM portfolio_clientperiodfact f
    WHERE f.client_id IN (SELECT client_id FROM manager_clients)
)
SELECT
    COALESCE(o.total_revenue, 0) + COALESCE(f.total_revenue, 0) AS total_revenue,
    COALESCE(o.total_profit, 0) + COALESCE(f.total_profit, 0) AS total_profit,
    COALESCE(o.current_year_revenue, 0) + COALESCE(f.current_year_revenue, 0) AS current_year_revenue,
    COALESCE(o.previous_year_revenue, 0) + COALESCE(f.previous_year_revenue, 0) AS previous_year_revenue,
    CASE
        WHEN COALESCE(o.previous_year_revenue, 0) + COALESCE(f.previous_year_revenue, 0) = 0 THEN NULL
        ELSE (
            COALESCE(o.current_year_revenue, 0) + COALESCE(f.current_year_revenue, 0)
            - COALESCE(o.previous_year_revenue, 0) - COALESCE(f.previous_year_revenue, 0)
        ) / (COALESCE(o.previous_year_revenue, 0) + COALESCE(f.previous_year_revenue, 0))
    END AS year_growth_rate,
    (SELECT COUNT(*) FROM manager_clients) AS total_clients,
    COALESCE(o.bought_clients, 0) + COALESCE(f.bought_clients, 0) AS bought_clients,
    CASE
        WHEN COALESCE(o.orders_count, 0) + COALESCE(f.orders_count, 0) = 0 THEN NULL
        ELSE (COALESCE(o.total_revenue, 0) + COALESCE(f.total_revenue, 0))
             / (COALESCE(o.orders_count, 0) + COALESCE(f.orders_count, 0))
    END AS average_check
FROM order_totals o CROSS JOIN fact_totals f;


-- Сравнение двух произвольных периодов A и B по клиентам менеджера.
WITH manager_clients AS (
    SELECT DISTINCT client_id
    FROM portfolio_clientmanagerassignment
    WHERE manager_id = :manager_id
),
period_a AS (
    SELECT client_id, SUM(revenue) AS revenue, SUM(orders_count) AS orders_count
    FROM portfolio_monthly_client_revenue
    WHERE client_id IN (SELECT client_id FROM manager_clients)
      AND period_start >= :period_a_start
      AND period_end <= :period_a_end
    GROUP BY client_id
),
period_b AS (
    SELECT client_id, SUM(revenue) AS revenue, SUM(orders_count) AS orders_count
    FROM portfolio_monthly_client_revenue
    WHERE client_id IN (SELECT client_id FROM manager_clients)
      AND period_start >= :period_b_start
      AND period_end <= :period_b_end
    GROUP BY client_id
)
SELECT
    c.name AS client_name,
    COALESCE(a.revenue, 0) AS revenue_period_a,
    COALESCE(b.revenue, 0) AS revenue_period_b,
    COALESCE(b.revenue, 0) - COALESCE(a.revenue, 0) AS absolute_delta,
    CASE
        WHEN COALESCE(a.revenue, 0) = 0 THEN NULL
        ELSE (COALESCE(b.revenue, 0) - COALESCE(a.revenue, 0)) / COALESCE(a.revenue, 0)
    END AS delta_percent
FROM portfolio_client c
JOIN manager_clients mc ON mc.client_id = c.id
LEFT JOIN period_a a ON a.client_id = c.id
LEFT JOIN period_b b ON b.client_id = c.id
ORDER BY revenue_period_b DESC;
