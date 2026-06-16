from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.db.models import Case, DecimalField, F, Value, When
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Coalesce
import re

from portfolio.forms import ClientForm, ClientPeriodFactForm, ExcelImportForm, ManagerForm, OrderForm, PeriodComparisonForm
from portfolio.models import (
    Client,
    ClientComment,
    ClientManagerAssignment,
    ClientPeriodFact,
    Manager,
    Notification,
    Order,
    OrderComment,
    OrderStage,
)
from portfolio.services.access import clients_for_manager, is_owner, manager_for_user, orders_for_user, visible_clients_for_user
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


DEFAULT_ORDER_STAGES = [
    'Новый',
    'КП отправлено',
    'Согласование',
    'Договор',
    'Предоплата',
    'В производстве',
    'Готов к отгрузке',
    'Отгружен',
    'Закрыт',
]


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
            'chart_api_url': f'/api/dashboard/charts/?manager_id={manager.id}',
            'total_revenue_all_time': _total_revenue_for_clients(clients_qs),
            'active_orders': _active_orders_for_user(request.user)[:8],
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
            'chart_api_url': '/api/dashboard/charts/',
            'total_revenue_all_time': _total_revenue_for_clients(Client.objects.all()),
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
            'chart_api_url': f'/api/dashboard/charts/?manager_id={manager.id}',
            'total_revenue_all_time': _total_revenue_for_clients(clients_qs),
            'active_orders': _active_orders_for_manager(manager)[:8],
            'readonly': True,
        },
    )


@login_required
def clients(request):
    query = request.GET.get('q', '').strip()
    sort = request.GET.get('sort', 'name')
    direction = request.GET.get('direction', 'asc')
    clients_qs = list(_client_table_queryset(request.user, query, sort, direction)[:80])
    for client in clients_qs:
        assignment = current_manager_for_client(client)
        client.table_manager = assignment.manager if assignment else None
        client.table_last_comment = _latest_client_comment_text(client)
        client.table_last_order = _last_client_order_date(client)
    return render(
        request,
        'portfolio/clients.html',
        {
            'clients': clients_qs,
            'query': query,
            'sort': sort,
            'direction': direction,
            'client_form': ClientForm(user=request.user),
        },
    )


@login_required
def client_search(request):
    query = request.GET.get('q', '').strip()
    rows = []
    for client in _search_clients(visible_clients_for_user(request.user), query)[:20]:
        assignment = current_manager_for_client(client)
        rows.append(
            {
                'id': client.id,
                'name': client.name,
                'inn': client.inn,
                'phone': client.phone,
                'email': client.email,
                'address': client.address,
                'manager': assignment.manager.full_name if assignment else '',
                'manager_id': assignment.manager_id if assignment else None,
                'last_comment': _latest_client_comment_text(client),
                'last_order': _format_date(_last_client_order_date(client)),
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
    notifications = Notification.objects.filter(recipient=request.user, client=client, is_read=False)
    notifications.update(is_read=True, read_at=timezone.now())
    comments = client.comments.select_related('author')
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
            'comments': comments[:5],
            'hidden_comments': comments[5:50],
            'comments_count': comments.count(),
            'hidden_comments_count': max(comments.count() - 5, 0),
            'active_projects': _active_projects_for_client(client),
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
    messages.error(request, _form_error_text(form, 'Клиент с такими данными уже существует.'))
    return render(
        request,
        'portfolio/clients.html',
        {'clients': visible_clients_for_user(request.user)[:50], 'client_form': form, 'query': '', 'sort': 'name', 'direction': 'asc'},
        status=400,
    )


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
        messages.error(request, _form_error_text(form, 'Не удалось сохранить клиента.'))
        return client_detail(request, client.id)
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
        messages.error(request, _form_error_text(form, 'Не удалось сохранить менеджера.'))
    else:
        form = ManagerForm()
    return render(request, 'portfolio/managers_admin.html', _managers_admin_context(form=form), status=400 if form.errors else 200)


def _managers_admin_context(form=None, edit_form=None, edit_manager_id=None):
    managers = (
        Manager.objects.filter(is_active=True)
        .annotate(clients_count=Count('client_assignments__client', distinct=True))
        .order_by('full_name')
    )
    return {
        'managers': managers,
        'form': form or ManagerForm(),
        'edit_form': edit_form,
        'edit_manager_id': edit_manager_id,
    }


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
        else:
            messages.error(request, _form_error_text(form, 'Не удалось обновить менеджера.'))
            return render(
                request,
                'portfolio/managers_admin.html',
                _managers_admin_context(edit_form=form, edit_manager_id=manager.id),
                status=400,
            )
    return redirect('managers_admin')


@login_required
@require_POST
def manager_delete(request, manager_id):
    if not is_owner(request.user):
        return redirect('manager_dashboard')
    manager = get_object_or_404(Manager, id=manager_id)
    if manager.client_assignments.exists() or manager.period_facts.exists():
        manager.is_active = False
        manager.save(update_fields=['is_active', 'updated_at'])
        messages.success(request, 'У менеджера есть история портфеля, поэтому он деактивирован.')
    else:
        manager.delete()
        messages.success(request, 'Менеджер удален.')
    return redirect('managers_admin')


@login_required
@require_GET
def validate_manager_email_api(request):
    email = (request.GET.get('email') or '').strip()
    exclude_id = request.GET.get('exclude_id')
    queryset = Manager.objects.filter(email__iexact=email) if email else Manager.objects.none()
    if exclude_id:
        queryset = queryset.exclude(id=exclude_id)
    return JsonResponse({'available': not queryset.exists(), 'message': '' if not queryset.exists() else 'Занято'})


@login_required
@require_GET
def validate_client_api(request):
    inn = (request.GET.get('inn') or '').strip()
    name = (request.GET.get('name') or '').strip()
    address = (request.GET.get('address') or '').strip()
    exclude_id = request.GET.get('exclude_id')
    queryset = Client.objects.all()
    if exclude_id:
        queryset = queryset.exclude(id=exclude_id)
    exists = False
    message = ''
    if inn:
        exists = queryset.filter(inn=inn).exists()
        message = 'Клиент с таким ИНН уже существует.' if exists else ''
    elif name and address:
        exists = queryset.filter(name__iexact=name, address__iexact=address).exists()
        message = 'Клиент с таким названием и адресом уже существует.' if exists else ''
    return JsonResponse({'available': not exists, 'message': message or ('Занято' if exists else '')})


@login_required
def active_orders(request):
    status_filter = request.GET.get('status', 'all')
    orders = _orders_for_user_list(request.user, status_filter)
    order_form = OrderForm(user=request.user)
    managers = Manager.objects.filter(is_active=True).order_by('full_name') if is_owner(request.user) else []
    Notification.objects.filter(recipient=request.user, kind=Notification.Kind.ORDER_COMMENT, is_read=False).update(
        is_read=True,
        read_at=timezone.now(),
    )
    return render(
        request,
        'portfolio/active_orders.html',
        {
            'orders': orders,
            'order_form': order_form,
            'managers': managers,
            'selected_status': status_filter,
            'can_assign_manager': is_owner(request.user),
        },
    )


@login_required
@require_POST
def order_create(request):
    form = OrderForm(request.POST, user=request.user)
    if not form.is_valid():
        messages.error(request, _form_error_text(form, 'Не удалось создать заказ.'))
        return redirect('active_orders')
    order = form.save()
    _ensure_default_stages(order)
    manager = form.cleaned_data.get('manager') if is_owner(request.user) else manager_for_user(request.user)
    if manager:
        _assign_client_manager(order.client, manager)
    comment = form.cleaned_data.get('initial_comment')
    if comment:
        OrderComment.objects.create(order=order, author=request.user, text=comment)
    messages.success(request, f'Заказ #{order.id} создан.')
    return redirect(f'/orders/active/#order-{order.id}')


@login_required
@require_POST
def order_update(request, order_id):
    order = _visible_order_or_404(request.user, order_id)
    form = OrderForm(request.POST, instance=order, user=request.user)
    if form.is_valid():
        form.save()
        messages.success(request, f'Заказ #{order.id} сохранен.')
    else:
        messages.error(request, _form_error_text(form, 'Не удалось сохранить заказ.'))
    return redirect(f'/orders/active/#order-{order.id}')


@login_required
@require_POST
def order_assign_manager(request, order_id):
    if not is_owner(request.user):
        return HttpResponseForbidden('Переназначать менеджера может только владелец.')
    order = _visible_order_or_404(request.user, order_id)
    manager = get_object_or_404(Manager, id=request.POST.get('manager'), is_active=True)
    _assign_client_manager(order.client, manager)
    messages.success(request, f'Клиент заказа #{order.id} переназначен на {manager.full_name}.')
    return redirect(f'/orders/active/#order-{order.id}')


@login_required
@require_GET
def dashboard_charts_api(request):
    manager_id = request.GET.get('manager_id')
    if manager_id:
        manager = get_object_or_404(Manager, id=manager_id)
        if not is_owner(request.user) and manager_for_user(request.user) != manager:
            return HttpResponseForbidden('Недостаточно прав.')
        client_ids = list(clients_for_manager(manager).values_list('id', flat=True))
    else:
        client_ids = list(visible_clients_for_user(request.user).values_list('id', flat=True))
    return JsonResponse({'charts': _dashboard_chart_payload(client_ids)})


@login_required
@require_POST
def client_comment_create(request, client_id):
    client = get_object_or_404(visible_clients_for_user(request.user), id=client_id)
    text = request.POST.get('text', '').strip()
    if not text:
        return JsonResponse({'error': 'Комментарий пустой.'}, status=400)
    comment = ClientComment.objects.create(client=client, author=request.user, text=text)
    _notify_client_comment(request.user, client, text)
    return JsonResponse({'comment': _comment_payload(comment)})


@login_required
@require_POST
def client_comment_delete(request, client_id, comment_id):
    client = get_object_or_404(visible_clients_for_user(request.user), id=client_id)
    comment = get_object_or_404(ClientComment, id=comment_id, client=client)
    if not is_owner(request.user) and comment.author_id != request.user.id:
        return HttpResponseForbidden('Удалить может автор или владелец.')
    comment.delete()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def order_comment_create(request, order_id):
    order = _visible_order_or_404(request.user, order_id)
    text = request.POST.get('text', '').strip()
    if not text:
        return JsonResponse({'error': 'Комментарий пустой.'}, status=400)
    comment = OrderComment.objects.create(order=order, author=request.user, text=text)
    _notify_order_comment(request.user, order, text)
    return JsonResponse({'comment': _comment_payload(comment)})


@login_required
@require_POST
def order_comment_delete(request, order_id, comment_id):
    order = _visible_order_or_404(request.user, order_id)
    comment = get_object_or_404(OrderComment, id=comment_id, order=order)
    if not is_owner(request.user) and comment.author_id != request.user.id:
        return HttpResponseForbidden('Удалить может автор или владелец.')
    comment.delete()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def order_stage_toggle(request, order_id, stage_id):
    order = _visible_order_or_404(request.user, order_id)
    stage = get_object_or_404(OrderStage, id=stage_id, order=order)
    is_done = request.POST.get('is_done') == 'true'
    stage.is_done = is_done
    stage.completed_at = timezone.now() if is_done else None
    stage.save(update_fields=['is_done', 'completed_at', 'updated_at'])
    order._prefetched_objects_cache = {}
    return JsonResponse({'stage': _stage_payload(stage), 'progress': _order_progress(order), 'current_stage': _stage_payload(_current_order_stage(order))})


@login_required
@require_POST
def order_stage_create(request, order_id):
    order = _visible_order_or_404(request.user, order_id)
    title = request.POST.get('title', '').strip()
    if not title:
        return JsonResponse({'error': 'Название стадии пустое.'}, status=400)
    insert_at = request.POST.get('insert_at', 'end')
    after_id = request.POST.get('position_after')
    position = _next_stage_position(order, insert_at, after_id)
    OrderStage.objects.filter(order=order, position__gte=position).update(position=F('position') + 1)
    stage = OrderStage.objects.create(order=order, title=title, position=position)
    order._prefetched_objects_cache = {}
    return JsonResponse({'stage': _stage_payload(stage), 'progress': _order_progress(order)})


@login_required
@require_POST
def order_stage_delete(request, order_id, stage_id):
    order = _visible_order_or_404(request.user, order_id)
    stage = get_object_or_404(OrderStage, id=stage_id, order=order)
    stage.delete()
    order._prefetched_objects_cache = {}
    _normalize_stage_positions(order)
    return JsonResponse({'ok': True, 'progress': _order_progress(order)})


@login_required
@require_POST
def order_stage_move(request, order_id, stage_id):
    order = _visible_order_or_404(request.user, order_id)
    stage = get_object_or_404(OrderStage, id=stage_id, order=order)
    direction = request.POST.get('direction')
    if direction not in {'up', 'down'}:
        return JsonResponse({'error': 'Некорректное направление.'}, status=400)
    stages = list(order.stages.order_by('position', 'id'))
    index = next((idx for idx, item in enumerate(stages) if item.id == stage.id), None)
    target_index = index - 1 if direction == 'up' else index + 1
    if index is None or target_index < 0 or target_index >= len(stages):
        return JsonResponse({'ok': True, 'stages': [_stage_payload(item) for item in stages]})
    other = stages[target_index]
    stage.position, other.position = other.position, stage.position
    stage.save(update_fields=['position', 'updated_at'])
    other.save(update_fields=['position', 'updated_at'])
    order._prefetched_objects_cache = {}
    return JsonResponse({'ok': True, 'stages': [_stage_payload(item) for item in order.stages.order_by('position', 'id')]})


@login_required
@require_GET
def manager_summary_api(request, manager_id):
    manager = get_object_or_404(Manager, id=manager_id)
    if not is_owner(request.user) and manager_for_user(request.user) != manager:
        return HttpResponseForbidden('Недостаточно прав.')
    clients = clients_for_manager(manager)
    client_ids = list(clients.values_list('id', flat=True))
    facts_revenue = ClientPeriodFact.objects.filter(client_id__in=client_ids).aggregate(value=Coalesce(Sum('revenue'), Decimal('0')))['value']
    orders_revenue = Order.objects.filter(client_id__in=client_ids).exclude(status=Order.Status.CANCELLED).aggregate(value=Coalesce(Sum('amount'), Decimal('0')))['value']
    return JsonResponse(
        {
            'id': manager.id,
            'full_name': manager.full_name,
            'email': manager.email,
            'phone': manager.phone,
            'age': manager.age,
            'clients_count': clients.count(),
            'revenue_total': float(facts_revenue + orders_revenue),
            'portfolio_url': f'/owner/dashboard/manager/{manager.id}/' if is_owner(request.user) else '/manager/dashboard/',
        }
    )


@login_required
@require_GET
def notifications_api(request):
    notifications = Notification.objects.filter(recipient=request.user, is_read=False)[:20]
    return JsonResponse(
        {
            'count': notifications.count(),
            'items': [
                {
                    'id': item.id,
                    'kind': item.kind,
                    'text': item.comment_text,
                    'url': _notification_url(item),
                    'created_at': item.created_at.strftime('%d.%m.%Y %H:%M'),
                }
                for item in notifications
            ],
        }
    )


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


def _client_table_queryset(user, query, sort, direction):
    sort_map = {
        'name': 'name',
        'inn': 'inn',
        'phone': 'phone',
        'email': 'email',
        'manager': 'manager_assignments__manager__full_name',
        'comment': 'comments__created_at',
        'last_order': 'last_order',
    }
    queryset = (
        _search_clients(visible_clients_for_user(user), query)
        .annotate(
            last_order=Max('orders__order_date'),
            last_fact=Max('period_facts__period_start'),
        )
        .prefetch_related('manager_assignments__manager', 'comments')
        .distinct()
    )
    field = sort_map.get(sort, 'name')
    if direction == 'desc':
        field = f'-{field}'
    return queryset.order_by(field, 'name')


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


def _dashboard_chart_payload(client_ids):
    monthly = {}
    fact_rows = (
        ClientPeriodFact.objects.filter(client_id__in=client_ids, revenue__gt=0)
        .values('period_start')
        .annotate(
            revenue=Coalesce(Sum('revenue'), Decimal('0')),
            orders=Coalesce(Sum('orders_count'), 0),
            active_clients=Count('client', distinct=True),
        )
        .order_by('period_start')
    )
    for row in fact_rows:
        key = row['period_start'].replace(day=1)
        monthly.setdefault(key, {'revenue': Decimal('0'), 'orders': 0, 'clients': set()})
        monthly[key]['revenue'] += row['revenue']
        monthly[key]['orders'] += row['orders'] or row['active_clients']
        monthly[key]['clients_count_from_facts'] = row['active_clients']

    order_rows = (
        Order.objects.filter(client_id__in=client_ids)
        .exclude(status=Order.Status.CANCELLED)
        .values('order_date')
        .annotate(revenue=Coalesce(Sum('amount'), Decimal('0')), orders=Count('id'), active_clients=Count('client', distinct=True))
        .order_by('order_date')
    )
    for row in order_rows:
        key = row['order_date'].replace(day=1)
        monthly.setdefault(key, {'revenue': Decimal('0'), 'orders': 0, 'clients': set()})
        monthly[key]['revenue'] += row['revenue']
        monthly[key]['orders'] += row['orders']
        monthly[key]['clients_count_from_orders'] = row['active_clients']

    if not monthly:
        return {'labels': [], 'revenue': [], 'active_clients': [], 'average_check': [], 'growth_rate': []}

    start = min(monthly)
    end = max(max(monthly), timezone.localdate().replace(day=1))
    months = []
    current = start
    while current <= end:
        months.append(current)
        current = _add_month(current)

    labels = [_month_label(month) for month in months]
    revenue_values = []
    active_values = []
    average_values = []
    growth_values = []
    previous_revenue = None
    for month in months:
        item = monthly.get(month, {'revenue': Decimal('0'), 'orders': 0})
        revenue = item['revenue']
        orders = item['orders'] or 0
        active = item.get('clients_count_from_orders') or item.get('clients_count_from_facts') or 0
        average = revenue / orders if orders else Decimal('0')
        if previous_revenue is None or previous_revenue == 0:
            growth = Decimal('0')
        else:
            growth = ((revenue - previous_revenue) / previous_revenue) * Decimal('100')
        previous_revenue = revenue
        revenue_values.append(float(revenue))
        active_values.append(active)
        average_values.append(float(average))
        growth_values.append(float(growth))

    return {
        'labels': labels,
        'revenue': revenue_values,
        'active_clients': active_values,
        'average_check': average_values,
        'growth_rate': growth_values,
    }


def _total_revenue_for_clients(clients):
    client_ids = list(clients.values_list('id', flat=True))
    fact_revenue = ClientPeriodFact.objects.filter(client_id__in=client_ids).aggregate(
        value=Coalesce(Sum('revenue'), Decimal('0'))
    )['value']
    order_revenue = Order.objects.filter(client_id__in=client_ids).exclude(status=Order.Status.CANCELLED).aggregate(
        value=Coalesce(Sum('amount'), Decimal('0'))
    )['value']
    return fact_revenue + order_revenue


def _add_month(value):
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _month_label(value):
    months = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек']
    return f'{months[value.month - 1]} {value.year}'


def _active_orders_for_user(user):
    if is_owner(user):
        return _active_orders_queryset(Order.objects.all())
    clients = visible_clients_for_user(user)
    return _active_orders_queryset(Order.objects.filter(client__in=clients))


def _active_orders_for_manager(manager):
    return _active_orders_queryset(Order.objects.filter(client__in=clients_for_manager(manager)))


def _orders_for_user_list(user, status_filter='all'):
    queryset = (
        orders_for_user(user)
        .exclude(status__in=[Order.Status.CANCELLED, Order.Status.CANCELLED_AFTER_PRODUCTION])
        .select_related('client')
        .prefetch_related('stages', 'comments__author', 'client__manager_assignments__manager')
        .order_by('-created_at')
    )
    orders = []
    for order in queryset:
        _ensure_default_stages(order)
        order.progress = _order_progress(order)
        order.current_stage = _current_order_stage(order)
        order.manager = _manager_for_order(order)
        order.is_active_project = _is_active_order(order)
        order.visible_comments = list(order.comments.select_related('author').all()[:5])
        order.hidden_comments_count = max(order.comments.count() - 5, 0)
        if status_filter == 'active' and not order.is_active_project:
            continue
        if status_filter == 'shipped' and (not order.current_stage or order.current_stage.title.strip().lower() != 'отгружен'):
            continue
        if status_filter == 'closed' and (not order.current_stage or order.current_stage.title.strip().lower() != 'закрыт'):
            continue
        orders.append(order)
    return orders


def _active_orders_queryset(queryset):
    candidate_orders = (
        queryset.exclude(status__in=[Order.Status.CANCELLED, Order.Status.CANCELLED_AFTER_PRODUCTION])
        .select_related('client')
        .prefetch_related('stages', 'comments__author', 'client__manager_assignments__manager')
        .order_by('-created_at')
    )
    orders = []
    for order in candidate_orders:
        _ensure_default_stages(order)
        order.progress = _order_progress(order)
        order.current_stage = _current_order_stage(order)
        order.manager = _manager_for_order(order)
        if _is_active_order(order):
            orders.append(order)
    return orders


def _active_projects_for_client(client):
    return _active_orders_queryset(Order.objects.filter(client=client).order_by('-order_date', '-id'))


def _is_active_order(order):
    stage = getattr(order, 'current_stage', None) or _current_order_stage(order)
    if not stage:
        return order.status not in [Order.Status.CANCELLED, Order.Status.CANCELLED_AFTER_PRODUCTION]
    return stage.title.strip().lower() not in {'закрыт', 'отгружен'}


def _ensure_default_stages(order):
    if order.stages.exists():
        return
    OrderStage.objects.bulk_create(
        [OrderStage(order=order, title=title, position=index) for index, title in enumerate(DEFAULT_ORDER_STAGES, start=1)]
    )


def _normalize_stage_positions(order):
    for index, stage in enumerate(order.stages.order_by('position', 'id'), start=1):
        if stage.position != index:
            stage.position = index
            stage.save(update_fields=['position', 'updated_at'])


def _next_stage_position(order, insert_at, after_id):
    if insert_at == 'start':
        return 1
    if insert_at == 'after' and after_id:
        try:
            previous = order.stages.get(id=after_id)
            return previous.position + 1
        except OrderStage.DoesNotExist:
            pass
    max_position = order.stages.aggregate(max_position=Max('position'))['max_position'] or 0
    return max_position + 1


def _assign_client_manager(client, manager):
    today = timezone.localdate()
    active = ClientManagerAssignment.objects.filter(client=client, valid_to__isnull=True)
    active.exclude(manager=manager).filter(valid_from__lt=today).update(valid_to=today - timedelta(days=1))
    active.exclude(manager=manager).filter(valid_from__gte=today).delete()
    ClientManagerAssignment.objects.update_or_create(
        client=client,
        manager=manager,
        valid_from=today,
        defaults={'valid_to': None},
    )


def _order_progress(order):
    stages = list(order.stages.all())
    total = len(stages)
    done = sum(1 for stage in stages if stage.is_done)
    return {'done': done, 'total': total, 'percent': int((done / total) * 100) if total else 0}


def _current_order_stage(order):
    stages = list(order.stages.all())
    done_stages = [stage for stage in stages if stage.is_done]
    if done_stages:
        return done_stages[-1]
    return stages[0] if stages else None


def _manager_for_order(order):
    return current_manager_for_client(order.client)


def _visible_order_or_404(user, order_id):
    queryset = Order.objects.select_related('client').prefetch_related('stages', 'comments')
    if not is_owner(user):
        queryset = queryset.filter(client__in=visible_clients_for_user(user))
    order = get_object_or_404(queryset, id=order_id)
    _ensure_default_stages(order)
    return order


def _stage_payload(stage):
    return {
        'id': stage.id,
        'title': stage.title,
        'position': stage.position,
        'is_done': stage.is_done,
        'completed_at': stage.completed_at.strftime('%d.%m.%Y %H:%M') if stage.completed_at else '',
    }


def _comment_payload(comment):
    author = comment.author
    return {
        'id': comment.id,
        'author': author.get_full_name() or author.username if author else 'Пользователь удален',
        'role': 'Owner' if author and is_owner(author) else 'Менеджер',
        'text': comment.text,
        'created_at': comment.created_at.strftime('%d.%m.%Y %H:%M'),
    }


def _notify_client_comment(actor, client, text):
    assignment = current_manager_for_client(client)
    if is_owner(actor) and assignment and assignment.manager.user and assignment.manager.user_id != actor.id:
        Notification.objects.create(
            recipient=assignment.manager.user,
            actor=actor,
            kind=Notification.Kind.CLIENT_COMMENT,
            client=client,
            comment_text=text[:500],
        )


def _notify_order_comment(actor, order, text):
    assignment = current_manager_for_client(order.client)
    if is_owner(actor) and assignment and assignment.manager.user and assignment.manager.user_id != actor.id:
        Notification.objects.create(
            recipient=assignment.manager.user,
            actor=actor,
            kind=Notification.Kind.ORDER_COMMENT,
            client=order.client,
            order=order,
            comment_text=text[:500],
        )


def _notification_url(notification):
    if notification.kind == Notification.Kind.ORDER_COMMENT and notification.order_id:
        return f'/orders/active/#order-{notification.order_id}'
    if notification.client_id:
        return f'/clients/{notification.client_id}/#comments'
    return '/'


def _latest_client_comment_text(client):
    comment = client.comments.order_by('-created_at').first()
    if comment:
        return comment.text[:90]
    return (client.manager_comment or client.internal_comment or '')[:90]


def _last_client_order_date(client):
    last_order = client.orders.aggregate(value=Max('order_date'))['value']
    last_fact = client.period_facts.aggregate(value=Max('period_start'))['value']
    value = max([item for item in [last_order, last_fact] if item], default=None)
    return value


def _format_date(value):
    return value.strftime('%d.%m.%Y') if value else ''


def _form_error_text(form, fallback):
    for errors in form.errors.values():
        if errors:
            return errors[0]
    return fallback
