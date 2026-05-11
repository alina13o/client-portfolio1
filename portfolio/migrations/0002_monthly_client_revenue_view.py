from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('portfolio', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE VIEW portfolio_monthly_client_revenue AS
                SELECT
                    client_id,
                    date_trunc('month', order_date)::date AS period_start,
                    (date_trunc('month', order_date) + interval '1 month - 1 day')::date AS period_end,
                    SUM(amount)::numeric(14, 2) AS revenue,
                    COUNT(*)::integer AS orders_count
                FROM portfolio_order
                WHERE status = 'completed'
                GROUP BY client_id, date_trunc('month', order_date)
                UNION ALL
                SELECT
                    client_id,
                    period_start,
                    period_end,
                    SUM(revenue)::numeric(14, 2) AS revenue,
                    SUM(orders_count)::integer AS orders_count
                FROM portfolio_clientperiodfact
                GROUP BY client_id, period_start, period_end;
            """,
            reverse_sql="DROP VIEW IF EXISTS portfolio_monthly_client_revenue;",
        ),
    ]
