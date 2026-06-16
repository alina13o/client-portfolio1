from django.db import migrations


def create_monthly_client_revenue_view(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(
            """
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
            """
        )
        return

    # SQLite rebuilds tables during later schema migrations. A dependent view
    # blocks that process, while the application calculates these metrics through
    # ORM queries. Keep the view PostgreSQL-only.
    return


def drop_monthly_client_revenue_view(apps, schema_editor):
    schema_editor.execute("DROP VIEW IF EXISTS portfolio_monthly_client_revenue;")


class Migration(migrations.Migration):
    dependencies = [
        ('portfolio', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(
            create_monthly_client_revenue_view,
            reverse_code=drop_monthly_client_revenue_view,
        ),
    ]
