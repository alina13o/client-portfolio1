from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from portfolio.models import Client, ClientManagerAssignment, ClientPeriodFact, Manager, Order
from portfolio.services.access import clients_for_manager, is_owner, manager_for_user, visible_clients_for_user


ZERO = Decimal('0.00')


@dataclass
class Period:
    start: date
    end: date


def compare_periods(user, period_1: Period, period_2: Period) -> dict:
    clients = visible_clients_for_user(user, as_of=period_2.end)
    client_ids = list(clients.values_list('id', flat=True))

    p1 = _period_metrics(client_ids, period_1)
    p2 = _period_metrics(client_ids, period_2)
    last_orders = _last_order_dates(client_ids, period_2.end)

    rows = []
    for client in clients:
        metrics_1 = p1.get(client.id, _empty_metrics())
        metrics_2 = p2.get(client.id, _empty_metrics())
        if metrics_1['revenue'] == 0 and metrics_2['revenue'] == 0 and metrics_1['orders'] == 0 and metrics_2['orders'] == 0:
            continue
        delta = metrics_2['revenue'] - metrics_1['revenue']
        growth_rate = _growth_rate(metrics_1['revenue'], metrics_2['revenue'])
        last_order = last_orders.get(client.id)
        segment, recommendation = _recommend(
            metrics_1=metrics_1,
            metrics_2=metrics_2,
            growth_rate=growth_rate,
            last_order=last_order,
            current_date=period_2.end,
        )
        rows.append(
            {
                'client': client,
                'revenue_p1': metrics_1['revenue'],
                'revenue_p2': metrics_2['revenue'],
                'orders_p1': metrics_1['orders'],
                'orders_p2': metrics_2['orders'],
                'delta_revenue': delta,
                'growth_rate': growth_rate,
                'last_order': last_order,
                'segment': segment,
                'recommendation': recommendation,
            }
        )

    rows.sort(key=lambda item: (item['delta_revenue'], item['revenue_p2']))
    summary = _summary(rows)
    return {'rows': rows, 'summary': summary, 'period_1': period_1, 'period_2': period_2}


def compare_periods_for_manager(manager: Manager, period_1: Period, period_2: Period) -> dict:
    clients = clients_for_manager(manager, as_of=period_2.end)
    return _compare_client_queryset(clients, period_1, period_2)


def compare_periods_for_owner(period_1: Period, period_2: Period) -> dict:
    return _compare_client_queryset(Client.objects.all(), period_1, period_2)


def _compare_client_queryset(clients, period_1: Period, period_2: Period) -> dict:
    client_ids = list(clients.values_list('id', flat=True))
    p1 = _period_metrics(client_ids, period_1)
    p2 = _period_metrics(client_ids, period_2)
    last_orders = _last_order_dates(client_ids, period_2.end)

    rows = []
    for client in clients:
        metrics_1 = p1.get(client.id, _empty_metrics())
        metrics_2 = p2.get(client.id, _empty_metrics())
        delta = metrics_2['revenue'] - metrics_1['revenue']
        growth_rate = _growth_rate(metrics_1['revenue'], metrics_2['revenue'])
        segment, recommendation = _recommend(
            metrics_1=metrics_1,
            metrics_2=metrics_2,
            growth_rate=growth_rate,
            last_order=last_orders.get(client.id),
            current_date=period_2.end,
        )
        rows.append(
            {
                'client': client,
                'revenue_p1': metrics_1['revenue'],
                'revenue_p2': metrics_2['revenue'],
                'orders_p1': metrics_1['orders'],
                'orders_p2': metrics_2['orders'],
                'delta_revenue': delta,
                'growth_rate': growth_rate,
                'last_order': last_orders.get(client.id),
                'segment': segment,
                'recommendation': recommendation,
            }
        )

    rows.sort(key=lambda item: item['revenue_p2'], reverse=True)
    return {'rows': rows, 'summary': _summary(rows), 'period_1': period_1, 'period_2': period_2}


def portfolio_kpis_for_manager(manager: Manager, today: date | None = None) -> dict:
    today = today or timezone.localdate()
    clients = clients_for_manager(manager, as_of=today)
    return _portfolio_kpis(clients, today, plan=manager.current_year_plan)


def portfolio_kpis_for_owner(today: date | None = None) -> dict:
    today = today or timezone.localdate()
    total_plan = Manager.objects.filter(is_active=True).aggregate(total=Coalesce(Sum('current_year_plan'), ZERO))['total']
    return _portfolio_kpis(Client.objects.all(), today, plan=total_plan)


def manager_ranking(period: Period) -> list[dict]:
    rows = []
    managers = Manager.objects.filter(is_active=True).order_by('full_name')
    for manager in managers:
        clients = clients_for_manager(manager, as_of=period.end)
        client_ids = list(clients.values_list('id', flat=True))
        metrics = _period_totals(client_ids, period)
        rows.append(
            {
                'manager': manager,
                'revenue': metrics['revenue'],
                'profit': metrics['profit'],
                'clients_count': clients.count(),
            }
        )
    rows.sort(key=lambda row: row['revenue'], reverse=True)
    return rows


def period_from_preset(preset: str, today: date | None = None) -> tuple[Period, Period]:
    today = today or timezone.localdate()
    if preset == 'quarter':
        current_start = _quarter_start(today)
        previous_end = current_start - timedelta(days=1)
        previous_start = _quarter_start(previous_end)
        return Period(previous_start, previous_end), Period(current_start, today)
    if preset == 'year':
        current_start = date(today.year, 1, 1)
        previous_start = date(today.year - 1, 1, 1)
        previous_end = date(today.year - 1, 12, 31)
        return Period(previous_start, previous_end), Period(current_start, today)
    previous, current = default_periods(today=today)
    return previous, current


def client_monthly_revenue(client: Client) -> list[dict]:
    rows = []
    order_rows = (
        Order.objects.filter(client=client, status=Order.Status.COMPLETED)
        .values('order_date__year', 'order_date__month')
        .annotate(revenue=Coalesce(Sum('amount'), ZERO))
        .order_by('order_date__year', 'order_date__month')
    )
    fact_rows = (
        ClientPeriodFact.objects.filter(client=client)
        .values('period_start')
        .annotate(revenue=Coalesce(Sum('revenue'), ZERO))
        .order_by('period_start')
    )
    monthly = {}
    for row in order_rows:
        key = date(row['order_date__year'], row['order_date__month'], 1)
        monthly[key] = monthly.get(key, ZERO) + row['revenue']
    for row in fact_rows:
        key = row['period_start'].replace(day=1)
        monthly[key] = monthly.get(key, ZERO) + row['revenue']
    max_revenue = max(monthly.values(), default=ZERO)
    for period_start, revenue in sorted(monthly.items()):
        rows.append({'period': period_start, 'revenue': revenue, 'height': _chart_height(revenue, max_revenue)})
    return rows


def client_revenue_rows(clients) -> list[dict]:
    client_ids = list(clients.values_list('id', flat=True))
    monthly_by_client = _monthly_revenue_by_client(client_ids)
    rows = []
    clients_by_id = {client.id: client for client in clients}
    for client_id, monthly_rows in monthly_by_client.items():
        total = sum((row['revenue'] for row in monthly_rows), ZERO)
        if total <= 0:
            continue
        rows.append(
            {
                'client': clients_by_id[client_id],
                'total_revenue': total,
                'periods': monthly_rows,
            }
        )
    rows.sort(key=lambda row: row['total_revenue'], reverse=True)
    return rows


def _monthly_revenue_by_client(client_ids: list[int]) -> dict[int, list[dict]]:
    monthly = {}
    order_rows = (
        Order.objects.filter(client_id__in=client_ids, status=Order.Status.COMPLETED)
        .values('client_id', 'order_date__year', 'order_date__month')
        .annotate(revenue=Coalesce(Sum('amount'), ZERO), orders=Count('id'))
        .order_by('order_date__year', 'order_date__month')
    )
    for row in order_rows:
        period = date(row['order_date__year'], row['order_date__month'], 1)
        key = (row['client_id'], period)
        monthly[key] = monthly.get(key, {'revenue': ZERO, 'orders': 0})
        monthly[key]['revenue'] += row['revenue']
        monthly[key]['orders'] += row['orders']

    fact_rows = (
        ClientPeriodFact.objects.filter(client_id__in=client_ids, revenue__gt=0)
        .values('client_id', 'period_start')
        .annotate(revenue=Coalesce(Sum('revenue'), ZERO), orders=Sum('orders_count'))
        .order_by('period_start')
    )
    for row in fact_rows:
        period = row['period_start'].replace(day=1)
        key = (row['client_id'], period)
        monthly[key] = monthly.get(key, {'revenue': ZERO, 'orders': 0})
        monthly[key]['revenue'] += row['revenue']
        monthly[key]['orders'] += row['orders'] or 0

    result = {}
    for (client_id, period), values in monthly.items():
        result.setdefault(client_id, []).append(
            {'period': period, 'revenue': values['revenue'], 'orders': values['orders']}
        )
    for rows in result.values():
        rows.sort(key=lambda row: row['period'])
    return result


def current_manager_for_client(client: Client, as_of: date | None = None):
    as_of = as_of or timezone.localdate()
    return (
        ClientManagerAssignment.objects.filter(
            client=client,
            valid_from__lte=as_of,
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=as_of))
        .select_related('manager')
        .order_by('-valid_from')
        .first()
    )


def _period_metrics(client_ids: list[int], period: Period) -> dict[int, dict]:
    profit_expr = ExpressionWrapper(
        F('amount') - F('cost_amount'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    order_metrics = {
        row['client_id']: {
            'revenue': row['revenue'] or ZERO,
            'profit': row['profit'] or ZERO,
            'orders': row['orders'] or 0,
        }
        for row in Order.objects.filter(
            client_id__in=client_ids,
            order_date__gte=period.start,
            order_date__lte=period.end,
            status=Order.Status.COMPLETED,
        )
        .values('client_id')
        .annotate(revenue=Sum('amount'), profit=Sum(profit_expr), orders=Count('id'))
    }

    fact_metrics = {
        row['client_id']: {
            'revenue': row['revenue'] or ZERO,
            'profit': row['revenue'] or ZERO,
            'orders': row['orders'] or 0,
        }
        for row in ClientPeriodFact.objects.filter(
            client_id__in=client_ids,
            period_start__gte=period.start,
            period_end__lte=period.end,
        )
        .values('client_id')
        .annotate(revenue=Sum('revenue'), orders=Sum('orders_count'))
    }

    metrics = {}
    for client_id in set(order_metrics) | set(fact_metrics):
        order_row = order_metrics.get(client_id, _empty_metrics())
        fact_row = fact_metrics.get(client_id, _empty_metrics())
        metrics[client_id] = {
            'revenue': order_row['revenue'] + fact_row['revenue'],
            'profit': order_row['profit'] + fact_row['profit'],
            'orders': order_row['orders'] + fact_row['orders'],
        }
    return metrics


def _portfolio_kpis(clients, today: date, plan: Decimal) -> dict:
    client_ids = list(clients.values_list('id', flat=True))
    current_year = Period(date(today.year, 1, 1), today)
    previous_year = Period(date(today.year - 1, 1, 1), date(today.year - 1, 12, 31))
    current = _period_totals(client_ids, current_year)
    previous = _period_totals(client_ids, previous_year)
    all_time = _period_totals(client_ids, Period(date(1900, 1, 1), today))
    total_clients = clients.count()
    bought_clients = _bought_clients(client_ids)
    return {
        'current_year_revenue': current['revenue'],
        'portfolio_plan': plan or ZERO,
        'previous_year_revenue': previous['revenue'],
        'year_growth_rate': _growth_rate(previous['revenue'], current['revenue']),
        'total_clients': total_clients,
        'bought_clients': bought_clients,
        'average_check': _safe_ratio(all_time['revenue'], Decimal(all_time['orders'])),
        'total_revenue': all_time['revenue'],
        'total_profit': all_time['profit'],
        'orders_count': all_time['orders'],
    }


def _period_totals(client_ids: list[int], period: Period) -> dict:
    metrics = _period_metrics(client_ids, period)
    return {
        'revenue': sum((row['revenue'] for row in metrics.values()), ZERO),
        'profit': sum((row['profit'] for row in metrics.values()), ZERO),
        'orders': sum((row['orders'] for row in metrics.values()), 0),
    }


def _bought_clients(client_ids: list[int]) -> int:
    order_clients = set(
        Order.objects.filter(client_id__in=client_ids, status=Order.Status.COMPLETED)
        .values_list('client_id', flat=True)
        .distinct()
    )
    fact_clients = set(
        ClientPeriodFact.objects.filter(client_id__in=client_ids, revenue__gt=0)
        .values_list('client_id', flat=True)
        .distinct()
    )
    return len(order_clients | fact_clients)


def _last_order_dates(client_ids: list[int], until: date) -> dict[int, date]:
    order_dates = {
        row['client_id']: row['last_order']
        for row in Order.objects.filter(client_id__in=client_ids, order_date__lte=until)
        .values('client_id')
        .annotate(last_order=Max('order_date'))
    }
    fact_dates = {
        row['client_id']: row['last_order']
        for row in ClientPeriodFact.objects.filter(client_id__in=client_ids, period_end__lte=until)
        .values('client_id')
        .annotate(last_order=Max('period_end'))
    }
    result = {}
    for client_id in set(order_dates) | set(fact_dates):
        dates = [value for value in (order_dates.get(client_id), fact_dates.get(client_id)) if value]
        result[client_id] = max(dates) if dates else None
    return result


def _summary(rows: list[dict]) -> dict:
    revenue_p1 = sum((row['revenue_p1'] for row in rows), ZERO)
    revenue_p2 = sum((row['revenue_p2'] for row in rows), ZERO)
    active_p1 = sum(1 for row in rows if row['revenue_p1'] > 0)
    active_p2 = sum(1 for row in rows if row['revenue_p2'] > 0)
    retained = sum(1 for row in rows if row['revenue_p1'] > 0 and row['revenue_p2'] > 0)
    return {
        'revenue_p1': revenue_p1,
        'revenue_p2': revenue_p2,
        'delta_revenue': revenue_p2 - revenue_p1,
        'growth_rate': _growth_rate(revenue_p1, revenue_p2),
        'active_clients_p1': active_p1,
        'active_clients_p2': active_p2,
        'retention_rate': _safe_ratio(Decimal(retained), Decimal(active_p1)),
        'churn_rate': None if active_p1 == 0 else Decimal('1.00') - _safe_ratio(Decimal(retained), Decimal(active_p1)),
    }


def _recommend(metrics_1: dict, metrics_2: dict, growth_rate, last_order, current_date: date) -> tuple[str, str]:
    revenue_1 = metrics_1['revenue']
    revenue_2 = metrics_2['revenue']
    days_since_order = (current_date - last_order).days if last_order else None

    if revenue_1 == 0 and revenue_2 > 0:
        return 'Новый / реактивированный', 'Закрепить контакт, заполнить карточку клиента и предложить повторный заказ.'
    if revenue_1 > 0 and revenue_2 == 0:
        return 'Отток', 'Связаться с клиентом, выяснить причину отсутствия заказов и предложить возвратное условие.'
    if revenue_2 >= Decimal('100000') and (growth_rate is None or growth_rate >= Decimal('-0.10')):
        return 'Ключевой клиент', 'Удерживать персональным сопровождением и контролировать регулярность заказов.'
    if growth_rate is not None and growth_rate <= Decimal('-0.30'):
        return 'Риск снижения', 'Проверить причину падения выручки и подготовить индивидуальное предложение.'
    if growth_rate is not None and growth_rate >= Decimal('0.30'):
        return 'Рост', 'Развивать клиента: предложить смежные категории и увеличить ассортимент.'
    if days_since_order is not None and days_since_order > 60:
        return 'Пауза в активности', 'Поставить задачу на контакт и уточнить ближайшие потребности.'
    return 'Стабильный', 'Продолжать плановые касания и отслеживать динамику среднего чека.'


def _empty_metrics() -> dict:
    return {'revenue': ZERO, 'profit': ZERO, 'orders': 0}


def _growth_rate(previous: Decimal, current: Decimal):
    if previous == 0:
        return None
    return (current - previous) / previous


def _safe_ratio(numerator: Decimal, denominator: Decimal):
    if denominator == 0:
        return None
    return numerator / denominator


def _quarter_start(day: date) -> date:
    month = ((day.month - 1) // 3) * 3 + 1
    return date(day.year, month, 1)


def _chart_height(value: Decimal, max_value: Decimal) -> int:
    if max_value <= 0:
        return 0
    return int((value / max_value) * Decimal('100'))


def default_periods(today: date | None = None) -> tuple[Period, Period]:
    today = today or timezone.localdate()
    this_month = Period(start=today.replace(day=1), end=today)
    previous_end = this_month.start - timedelta(days=1)
    previous_month = Period(start=previous_end.replace(day=1), end=previous_end)
    return previous_month, this_month
