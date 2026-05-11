from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Coalesce
import re

from portfolio.forms import ClientForm, ClientPeriodFactForm, ExcelImportForm, ManagerForm, PeriodComparisonForm
from portfolio.models import Client, ClientManagerAssignment, ClientPeriodFact, Manager, Order
from portfolio.services.access import clients_for_manager, is_owner, manager_for_user, visible_clients_for_user
from portfolio.services.analytics import (
    Period,
    client_monthly_revenue,
    client_revenue_rows,
    compare_periods_for_manager,
    compare_periods_for_owner,
    current_manager_for_client,
    manager_ranking,
    period_from_preset,
    portfolio_kpis_for_manager,
    portfolio_kpis_for_owner,
)
from portfolio.services.importer import import_excel_workbook


@login_required
def home(request):
    if is_owner(request.user):
        return redirect('owner_dashboard')
    return redirect('manager_dashboard')


@login_required
def manager_dashboard(request):
    manager = manager_for_user(request.user)
    if manager is None:
        messages.error(request, 'Для пользователя не создан профиль менеджера.')
        return redirect('clients')

    clients_qs = clients_for_manager(manager)
    return render(
        request,
        'portfolio/manager_dashboard.html',
        {
            'manager': manager,
            'kpis': portfolio_kpis_for_manager(manager),
            'revenue_rows': client_revenue_rows(clients_qs),
            'readonly': False,
        },
    )


@login_required
def owner_dashboard(request):
    if not is_owner(request.user):
        return redirect('manager_dashboard')

    period_1, period_2, form = _periods_from_request(request)
    return render(
        request,
        'portfolio/owner_dashboard.html',
        {
            'kpis': portfolio_kpis_for_owner(),
            'comparison': compare_periods_for_owner(period_1, period_2),
            'ranking': manager_ranking(period_2),
            'form': form,
        },
    )


@login_required
def owner_manager_detail(request, manager_id):
    if not is_owner(request.user):
        return redirect('manager_dashboard')

    manager = get_object_or_404(Manager, id=manager_id)
    clients_qs = clients_for_manager(manager)
    return render(
        request,
        'portfolio/manager_dashboard.html',
        {
            'manager': manager,
            'kpis': portfolio_kpis_for_manager(manager),
            'revenue_rows': client_revenue_rows(clients_qs),
            'readonly': True,
        },
    )


@login_required
def clients(request):
    query = request.GET.get('q', '').strip()
    clients_qs = _search_clients(visible_clients_for_user(request.user), query)[:50]
    return render(
        request,
        'portfolio/clients.html',
        {
            'clients': clients_qs,
            'query': query,
            'client_form': ClientForm(user=request.user),
        },
    )


@login_required
def client_search(request):
    query = request.GET.get('q', '').strip()
    rows = []
    for client in _search_clients(visible_clients_for_user(request.user), query)[:20]:
        rows.append(
            {
                'id': client.id,
                'name': client.name,
                'inn': client.inn,
                'address': client.address,
                'highlight': _highlight_match(client.name, query),
                'url': f'/clients/{client.id}/',
            }
        )
    return JsonResponse({'results': rows})


@login_required
def client_detail(request, client_id):
    client = get_object_or_404(visible_clients_for_user(request.user), id=client_id)
    orders = Order.objects.filter(client=client).select_related('category').order_by('-order_date', '-id')
    monthly_revenue = client_monthly_revenue(client)
    order_totals = orders.aggregate(revenue=Sum('amount'), cost=Sum('cost_amount'), last_order=Max('order_date'))
    last_monthly = monthly_revenue[-1]['period'] if monthly_revenue else None
    totals = {
        'revenue': order_totals['revenue'] or sum((row['revenue'] for row in monthly_revenue), 0),
        'cost': order_totals['cost'],
        'last_order': order_totals['last_order'] or last_monthly,
    }
    assignment = current_manager_for_client(client)
    return render(
        request,
        'portfolio/client_detail.html',
        {
            'client': client,
            'orders': orders,
            'totals': totals,
            'assignment': assignment,
            'monthly_revenue': monthly_revenue,
            'client_form': ClientForm(instance=client, user=request.user),
            'fact_form': ClientPeriodFactForm(),
            'can_delete_orders': is_owner(request.user),
        },
    )


@login_required
def client_create(request):
    if request.method != 'POST':
        return redirect('clients')
    form = ClientForm(request.POST, user=request.user)
    if form.is_valid():
        client = form.save()
        messages.success(request, 'Клиент создан.')
        return redirect('client_detail', client_id=client.id)
    messages.error(request, 'Проверьте поля клиента.')
    return render(request, 'portfolio/clients.html', {'clients': visible_clients_for_user(request.user)[:50], 'client_form': form, 'query': ''})


@login_required
def client_update(request, client_id):
    client = get_object_or_404(visible_clients_for_user(request.user), id=client_id)
    if request.method != 'POST':
        return redirect('client_detail', client_id=client.id)
    form = ClientForm(request.POST, instance=client, user=request.user)
    if form.is_valid():
        form.save()
        messages.success(request, 'Клиент сохранен.')
    else:
        messages.error(request, 'Не удалось сохранить клиента.')
    return redirect('client_detail', client_id=client.id)


@login_required
def client_delete(request, client_id):
    client = get_object_or_404(visible_clients_for_user(request.user), id=client_id)
    has_orders = client.orders.exists() or client.period_facts.exists()
    if request.method != 'POST':
        return redirect('client_detail', client_id=client.id)
    if has_orders and not is_owner(request.user):
        messages.error(request, 'У клиента есть заказы. Удалять такие записи может только владелец.')
        return redirect('client_detail', client_id=client.id)
    if has_orders and request.POST.get('confirm_orders') != 'yes':
        return render(request, 'portfolio/confirm_delete_client.html', {'client': client})
    client.delete()
    messages.success(request, 'Клиент удален.')
    return redirect('clients')


@login_required
def fact_create(request, client_id):
    client = get_object_or_404(visible_clients_for_user(request.user), id=client_id)
    form = ClientPeriodFactForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        fact = form.save(commit=False)
        fact.client = client
        fact.period_end = _month_end(fact.period_start)
        assignment = current_manager_for_client(client)
        fact.manager = assignment.manager if assignment else manager_for_user(request.user)
        fact.save()
        messages.success(request, 'Заказ добавлен.')
    else:
        messages.error(request, 'Не удалось добавить заказ.')
    return redirect('client_detail', client_id=client.id)


@login_required
def fact_update(request, client_id, fact_id):
    client = get_object_or_404(visible_clients_for_user(request.user), id=client_id)
    fact = get_object_or_404(ClientPeriodFact, id=fact_id, client=client)
    form = ClientPeriodFactForm(request.POST or None, instance=fact)
    if request.method == 'POST' and form.is_valid():
        fact = form.save(commit=False)
        fact.period_end = _month_end(fact.period_start)
        fact.save()
        messages.success(request, 'Заказ сохранен.')
    else:
        messages.error(request, 'Не удалось сохранить заказ.')
    return redirect('client_detail', client_id=client.id)


@login_required
def fact_delete(request, client_id, fact_id):
    client = get_object_or_404(visible_clients_for_user(request.user), id=client_id)
    fact = get_object_or_404(ClientPeriodFact, id=fact_id, client=client)
    if request.method == 'POST':
        fact.delete()
        messages.success(request, 'Заказ удален.')
    return redirect('client_detail', client_id=client.id)


@login_required
def managers_admin(request):
    if not is_owner(request.user):
        return redirect('manager_dashboard')
    if request.method == 'POST':
        form = ManagerForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Менеджер сохранен.')
            return redirect('managers_admin')
    else:
        form = ManagerForm()
    managers = (
        Manager.objects.all()
        .annotate(clients_count=Count('client_assignments__client', distinct=True))
        .order_by('full_name')
    )
    return render(request, 'portfolio/managers_admin.html', {'managers': managers, 'form': form})


@login_required
def manager_update(request, manager_id):
    if not is_owner(request.user):
        return redirect('manager_dashboard')
    manager = get_object_or_404(Manager, id=manager_id)
    if request.method == 'POST':
        form = ManagerForm(request.POST, instance=manager)
        if form.is_valid():
            form.save()
            messages.success(request, 'Менеджер обновлен.')
    return redirect('managers_admin')


@login_required
def analytics(request):
    period = _chart_period(request)
    clients = visible_clients_for_user(request.user)
    client_ids = list(clients.values_list('id', flat=True))
    facts = ClientPeriodFact.objects.filter(client_id__in=client_ids, revenue__gt=0)
    if period:
        facts = facts.filter(period_start__gte=period[0], period_start__lte=period[1])
    monthly = _monthly_chart(facts)
    top_clients = _top_clients_chart(facts)
    groups = [{'name': 'Без группы', 'value': sum((row['value'] for row in top_clients), Decimal('0'))}]
    manager = manager_for_user(request.user)
    return render(
        request,
        'portfolio/analytics.html',
        {
            'monthly': monthly,
            'top_clients': top_clients,
            'groups': groups,
            'selected_period': request.GET.get('period', 'year'),
            'manager': manager,
        },
    )


@login_required
def upload_excel(request):
    if request.method == 'POST':
        form = ExcelImportForm(request.POST, request.FILES)
        if form.is_valid():
            result = import_excel_workbook(form.cleaned_data['file'], user=request.user)
            if result.batch.status == result.batch.Status.IMPORTED:
                messages.success(
                    request,
                    (
                        f'Импорт завершен: заказов {result.imported_orders}, '
                        f'клиентов в портфеле {result.imported_clients}, '
                        f'помесячных фактов {result.imported_period_facts}, '
                        f'ошибочных строк {result.failed_rows}. '
                        f'Всего добавлено/обновлено строк: {result.batch.rows_imported}.'
                    ),
                )
            else:
                messages.error(request, 'Файл загружен, но данные не удалось распознать. Детали сохранены в журнале импорта.')
            return redirect('manager_dashboard' if not is_owner(request.user) else 'owner_dashboard')
    else:
        form = ExcelImportForm()
    return render(request, 'portfolio/upload.html', {'form': form})


def _periods_from_request(request):
    preset = request.GET.get('preset') or PeriodComparisonForm.Preset.MONTH
    preset_period_1, preset_period_2 = period_from_preset(preset)
    initial = {
        'preset': preset,
        'period_1_start': preset_period_1.start,
        'period_1_end': preset_period_1.end,
        'period_2_start': preset_period_2.start,
        'period_2_end': preset_period_2.end,
    }
    form = PeriodComparisonForm(request.GET or None, initial=initial)
    if form.is_valid() and form.cleaned_data.get('preset') == PeriodComparisonForm.Preset.CUSTOM:
        return (
            Period(form.cleaned_data['period_1_start'], form.cleaned_data['period_1_end']),
            Period(form.cleaned_data['period_2_start'], form.cleaned_data['period_2_end']),
            form,
        )
    if form.is_valid() and form.cleaned_data.get('preset') != PeriodComparisonForm.Preset.CUSTOM:
        return (*period_from_preset(form.cleaned_data['preset']), form)
    return preset_period_1, preset_period_2, form


def _search_clients(queryset, query):
    queryset = queryset.order_by('name')
    if not query:
        return queryset
    db_matches = queryset.filter(
        Q(name__icontains=query)
        | Q(inn__icontains=query)
        | Q(address__icontains=query)
        | Q(external_code__icontains=query)
    )
    normalized_query = _normalize_search_text(query)
    if not normalized_query:
        return db_matches
    extra_ids = [
        client.id
        for client in queryset
        if normalized_query in _normalize_search_text(client.name)
        or normalized_query in _normalize_search_text(client.inn)
        or normalized_query in _normalize_search_text(client.address)
    ]
    return queryset.filter(Q(id__in=list(db_matches.values_list('id', flat=True))) | Q(id__in=extra_ids)).order_by('name')


def _normalize_search_text(value):
    value = str(value or '').lower()
    value = value.replace('«', '"').replace('»', '"').replace('“', '"').replace('”', '"')
    value = re.sub(r'[^0-9a-zа-яё]+', '', value)
    return value


def _highlight_match(text, query):
    if not query:
        return text
    pattern = re.escape(query)
    return re.sub(f'({pattern})', r'<mark>\1</mark>', text, flags=re.IGNORECASE)


def _month_end(period_start):
    if period_start.month == 12:
        return date(period_start.year + 1, 1, 1) - timedelta(days=1)
    return date(period_start.year, period_start.month + 1, 1) - timedelta(days=1)


def _chart_period(request):
    value = request.GET.get('period', 'year')
    today = timezone.localdate()
    if value == 'all':
        return None
    if value == '6':
        start = today.replace(day=1) - timedelta(days=180)
        return start.replace(day=1), today
    if value == 'custom':
        try:
            return date.fromisoformat(request.GET['start']), date.fromisoformat(request.GET['end'])
        except Exception:
            return date(today.year, 1, 1), today
    return today.replace(day=1) - timedelta(days=365), today


def _monthly_chart(facts):
    rows = (
        facts.values('period_start')
        .annotate(value=Coalesce(Sum('revenue'), Decimal('0')))
        .order_by('period_start')
    )
    max_value = max((row['value'] for row in rows), default=Decimal('0'))
    return [
        {
            'label': row['period_start'].strftime('%m.%Y'),
            'value': row['value'],
            'height': int((row['value'] / max_value) * 100) if max_value else 0,
        }
        for row in rows
    ]


def _top_clients_chart(facts):
    rows = (
        facts.values('client__name')
        .annotate(value=Coalesce(Sum('revenue'), Decimal('0')))
        .order_by('-value')[:10]
    )
    max_value = max((row['value'] for row in rows), default=Decimal('0'))
    return [
        {
            'label': row['client__name'][:34],
            'value': row['value'],
            'width': int((row['value'] / max_value) * 100) if max_value else 0,
        }
        for row in rows
    ]
