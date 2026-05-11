from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import re
from typing import Any

import pandas as pd
from django.db import transaction

from portfolio.models import (
    Category,
    Client,
    ClientManagerAssignment,
    ClientPeriodFact,
    ImportBatch,
    Manager,
    Order,
    StagingOrder,
)


COLUMN_ALIASES = {
    'manager_name': {'менеджер', 'manager', 'фамилия менеджера'},
    'client_code': {'код клиента', 'client code', 'client_code'},
    'inn': {'инн', 'inn'},
    'client_name': {'название клиента', 'клиент', 'client', 'client_name', 'название', 'контрагент'},
    'address': {'адрес', 'адрес доставки', 'адрес клиента'},
    'sales_channel': {'канал продаж', 'канал', 'источник', 'source'},
    'external_order_id': {'id заказа', 'номер заказа', 'заказ', 'order id', 'external_order_id'},
    'order_date': {'дата заказа', 'order date', 'order_date', 'дата', 'дата отгрузки'},
    'amount': {'сумма заказа', 'сумма', 'amount', 'выручка'},
    'cost_amount': {'себестоимость', 'cost', 'cost_amount', 'закупка'},
    'positions_count': {'количество позиций', 'позиции', 'positions_count', 'qty'},
    'category_name': {'категория товара/услуги', 'категория', 'category'},
    'nomenclature_group': {'номенклатурная группа', 'номенклатура', 'группа номенклатуры'},
    'client_status': {'статус клиента', 'client status'},
    'note': {'примечание', 'комментарий', 'note'},
    'order_status': {'статус заказа', 'order status'},
}

MONTHS = {
    'январь': 1,
    'янв': 1,
    'февраль': 2,
    'фев': 2,
    'март': 3,
    'мар': 3,
    'апрель': 4,
    'апр': 4,
    'май': 5,
    'июнь': 6,
    'июн': 6,
    'июль': 7,
    'июл': 7,
    'август': 8,
    'авг': 8,
    'сентябрь': 9,
    'сен': 9,
    'октябрь': 10,
    'окт': 10,
    'ноябрь': 11,
    'ноя': 11,
    'декабрь': 12,
    'дек': 12,
}

MARIA_MANAGER_NAME = 'Горбикова Мария Викторовна'


@dataclass
class ImportResult:
    batch: ImportBatch
    imported_clients: int
    imported_orders: int
    imported_period_facts: int
    failed_rows: int


def import_excel_workbook(uploaded_file, user=None) -> ImportResult:
    batch = ImportBatch.objects.create(
        filename=getattr(uploaded_file, 'name', 'uploaded.xlsx'),
        uploaded_by=user if getattr(user, 'is_authenticated', False) else None,
    )

    try:
        workbook = pd.read_excel(uploaded_file, sheet_name=None, header=None)
    except Exception as exc:
        batch.status = ImportBatch.Status.FAILED
        batch.errors = {'file': str(exc)}
        batch.save(update_fields=['status', 'errors', 'updated_at'])
        return ImportResult(batch=batch, imported_clients=0, imported_orders=0, imported_period_facts=0, failed_rows=0)

    default_manager_name = _preferred_manager_name(workbook, uploaded_file, user)
    imported_clients = 0
    imported_profiles = 0
    sheet_log = {}
    for sheet_name, raw_frame in workbook.items():
        if _looks_like_portfolio_client_list(sheet_name):
            clients_count = _import_portfolio_client_list(batch, raw_frame, default_manager_name)
            imported_clients += clients_count
            sheet_log[sheet_name] = {
                'status': 'portfolio_clients_imported' if clients_count else 'skipped',
                'rows_read': len(raw_frame),
                'clients_imported_or_confirmed': clients_count,
            }
        elif _looks_like_client_profile_sheet(sheet_name):
            profiles_count = _import_client_profile_sheet(batch, raw_frame, default_manager_name)
            imported_profiles += profiles_count
            sheet_log[sheet_name] = {
                'status': 'client_profiles_imported' if profiles_count else 'skipped',
                'rows_read': len(raw_frame),
                'profiles_imported_or_updated': profiles_count,
            }

    imported_orders = 0
    failed_rows = 0
    for sheet_name, raw_frame in workbook.items():
        normalized = _extract_order_table(raw_frame)
        if normalized is None:
            sheet_log[sheet_name] = {'status': 'skipped', 'reason': 'Не найдена строка заголовков заказов.'}
            continue
        frame, header_row = normalized
        mapped = _map_columns(frame)
        if not {'client_name', 'order_date', 'amount'}.issubset(mapped):
            sheet_log[sheet_name] = {'status': 'skipped', 'reason': 'Нет обязательных колонок клиент/дата/сумма.'}
            continue
        orders, failures = _import_order_sheet(batch, frame, mapped, default_manager_name)
        imported_orders += orders
        failed_rows += failures
        sheet_log[sheet_name] = {
            'status': 'orders_imported',
            'header_row': header_row + 1,
            'rows_read': len(frame),
            'rows_imported': orders,
            'rows_failed': failures,
        }

    imported_period_facts = 0
    if imported_orders == 0:
        for sheet_name, raw_frame in workbook.items():
            if _looks_like_monthly_portfolio(sheet_name, raw_frame):
                facts = _import_monthly_portfolio_sheet(batch, raw_frame, default_manager_name)
                imported_period_facts += facts
                sheet_log[sheet_name] = {
                    'status': 'period_facts_imported' if facts else 'skipped',
                    'rows_read': len(raw_frame),
                    'facts_imported': facts,
                }

    batch.rows_imported = imported_clients + imported_profiles + imported_orders + imported_period_facts
    batch.rows_failed = failed_rows
    batch.status = ImportBatch.Status.IMPORTED if batch.rows_imported else ImportBatch.Status.FAILED
    batch.errors = {
        **(batch.errors or {}),
        'sheets': sheet_log,
        'summary': {
            'orders_imported': imported_orders,
            'clients_imported_or_confirmed': imported_clients,
            'client_profiles_imported_or_updated': imported_profiles,
            'period_facts_imported': imported_period_facts,
            'rows_failed': failed_rows,
        },
    }
    if batch.rows_imported == 0:
        batch.errors['workbook'] = 'Не найден лист с колонками заказа или распознаваемая помесячная сводка.'
    batch.save()
    return ImportResult(
        batch=batch,
        imported_clients=imported_clients,
        imported_orders=imported_orders,
        imported_period_facts=imported_period_facts,
        failed_rows=failed_rows,
    )


def _extract_order_table(raw_frame: pd.DataFrame) -> tuple[pd.DataFrame, int] | None:
    scan_limit = min(len(raw_frame), 30)
    best_row = None
    best_score = 0
    for row_number in range(scan_limit):
        values = {_normalize_header(value) for value in raw_frame.iloc[row_number].tolist()}
        score = 0
        for aliases in COLUMN_ALIASES.values():
            if any(_normalize_header(alias) in values for alias in aliases):
                score += 1
        if score > best_score:
            best_score = score
            best_row = row_number

    if best_row is None or best_score < 3:
        return None

    columns = [
        str(value).strip() if not pd.isna(value) and str(value).strip() else f'column_{idx + 1}'
        for idx, value in enumerate(raw_frame.iloc[best_row].tolist())
    ]
    frame = raw_frame.iloc[best_row + 1 :].copy()
    frame.columns = columns
    frame = frame.dropna(how='all')
    return frame, best_row


def _preferred_manager_name(workbook: dict[str, pd.DataFrame], uploaded_file, user=None) -> str:
    filename = getattr(uploaded_file, 'name', '')
    filename_lower = filename.lower()
    if 'мари' in filename_lower:
        return MARIA_MANAGER_NAME

    if getattr(user, 'is_authenticated', False):
        try:
            return user.manager_profile.full_name
        except Manager.DoesNotExist:
            pass

    inferred = _infer_workbook_manager_name(workbook)
    if inferred:
        return inferred

    filename = re.sub(r'\.(xlsx|xls)$', '', filename, flags=re.IGNORECASE)
    filename = filename.replace('Портфель', '').strip()
    return filename or 'Не указан'


def _infer_workbook_manager_name(workbook: dict[str, pd.DataFrame]) -> str:
    for frame in workbook.values():
        for value in frame.head(20).to_numpy().flatten():
            text = _value_to_text(value)
            if _looks_like_person_name(text):
                return text
    return ''


def _looks_like_person_name(text: str) -> bool:
    if not text or len(text) > 120:
        return False
    lowered = text.lower()
    blocked = (
        'выручка',
        'портфель',
        'карточ',
        'комментар',
        'название',
        'клиент',
        'http',
        'заказ',
        'информац',
        'адрес',
        'выполнение',
    )
    if any(token in lowered for token in blocked):
        return False
    if lowered.startswith(('ооо ', 'ао ', 'ано ', 'ип ', 'зао ', 'бф ', 'пао ')):
        return False
    words = [word for word in re.split(r'\s+', text.replace('"', ' ').strip()) if word]
    if len(words) != 3:
        return False
    return all(re.fullmatch(r'[А-ЯЁ][а-яё-]+', word) for word in words)


def _map_columns(frame: pd.DataFrame) -> dict[str, str]:
    result = {}
    normalized_columns = {_normalize_header(column): column for column in frame.columns}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            column = normalized_columns.get(_normalize_header(alias))
            if column is not None:
                result[target] = column
                break
    return result


def _import_order_sheet(
    batch: ImportBatch,
    frame: pd.DataFrame,
    mapped: dict[str, str],
    default_manager_name: str,
) -> tuple[int, int]:
    imported = 0
    failed = 0
    batch.rows_total += len(frame)
    batch.save(update_fields=['rows_total', 'updated_at'])

    with transaction.atomic():
        for index, row in frame.iterrows():
            row_number = int(index) + 2
            staging = _create_staging_row(batch, row_number, row, mapped)
            errors = _validate_staging(staging)
            if errors:
                staging.validation_errors = errors
                staging.save(update_fields=['validation_errors', 'updated_at'])
                failed += 1
                continue

            manager = _get_or_create_manager(staging.manager_name or default_manager_name)
            client = _get_or_create_client(staging)
            order_date = _parse_date(staging.order_date)
            category = _get_or_create_category(staging.category_name)
            _ensure_assignment(client, manager, order_date)

            Order.objects.create(
                external_order_id=_cell(row, mapped.get('external_order_id')),
                client=client,
                order_date=order_date,
                amount=_parse_decimal(staging.amount),
                cost_amount=_parse_decimal(staging.cost_amount) or Decimal('0.00'),
                positions_count=_parse_int(staging.positions_count),
                category=category,
                nomenclature_group=staging.nomenclature_group,
                status=_normalize_order_status(row.get(mapped.get('order_status', ''), '')),
                note=staging.note,
                import_batch=batch,
            )
            imported += 1

    return imported, failed


def _create_staging_row(batch: ImportBatch, row_number: int, row: pd.Series, mapped: dict[str, str]) -> StagingOrder:
    return StagingOrder.objects.create(
        import_batch=batch,
        row_number=row_number,
        manager_name=_cell(row, mapped.get('manager_name')),
        client_code=_cell(row, mapped.get('client_code')),
        inn=_cell(row, mapped.get('inn')),
        client_name=_cell(row, mapped.get('client_name')),
        address=_cell(row, mapped.get('address')),
        sales_channel=_cell(row, mapped.get('sales_channel')),
        external_order_id=_cell(row, mapped.get('external_order_id')),
        order_date=_cell(row, mapped.get('order_date')),
        amount=_cell(row, mapped.get('amount')),
        cost_amount=_cell(row, mapped.get('cost_amount')),
        positions_count=_cell(row, mapped.get('positions_count')),
        category_name=_cell(row, mapped.get('category_name')),
        nomenclature_group=_cell(row, mapped.get('nomenclature_group')),
        client_status=_cell(row, mapped.get('client_status')),
        note=_cell(row, mapped.get('note')),
    )


def _validate_staging(staging: StagingOrder) -> list[str]:
    errors = []
    if not staging.client_name:
        errors.append('Не указано название клиента.')
    if _parse_date(staging.order_date) is None:
        errors.append('Некорректная дата заказа.')
    amount = _parse_decimal(staging.amount)
    if amount is None:
        errors.append('Некорректная сумма заказа.')
    elif amount < 0:
        errors.append('Сумма заказа не может быть отрицательной.')
    return errors


def _get_or_create_manager(manager_name: str) -> Manager:
    name = manager_name.strip() or 'Не указан'
    manager, _ = Manager.objects.get_or_create(full_name=name, defaults={'email': ''})
    return manager


def _get_or_create_client(staging: StagingOrder) -> Client:
    status = Client.Status.INACTIVE if staging.client_status.lower() == 'неактивен' else Client.Status.ACTIVE
    lookup = {'external_code': staging.client_code} if staging.client_code else {'name': staging.client_name}
    client, created = Client.objects.get_or_create(
        **lookup,
        defaults={
            'name': staging.client_name,
            'inn': staging.inn,
            'address': staging.address,
            'sales_channel': staging.sales_channel,
            'status': status,
        },
    )
    updates = []
    if staging.client_code and client.external_code != staging.client_code:
        client.external_code = staging.client_code
        updates.append('external_code')
    if client.name != staging.client_name:
        client.name = staging.client_name
        updates.append('name')
    if staging.inn and client.inn != staging.inn:
        client.inn = staging.inn
        updates.append('inn')
    if staging.address and client.address != staging.address:
        client.address = staging.address
        updates.append('address')
    if staging.sales_channel and client.sales_channel != staging.sales_channel:
        client.sales_channel = staging.sales_channel
        updates.append('sales_channel')
    if client.status != status:
        client.status = status
        updates.append('status')
    if not created and updates:
        updates.append('updated_at')
        client.save(update_fields=updates)
    return client


def _get_or_create_category(category_name: str):
    if not category_name:
        return None
    category, _ = Category.objects.get_or_create(name=category_name.strip())
    return category


def _ensure_assignment(client: Client, manager: Manager, valid_from: date):
    open_assignment = (
        ClientManagerAssignment.objects.filter(client=client, valid_to__isnull=True)
        .order_by('-valid_from')
        .first()
    )
    if open_assignment and open_assignment.manager_id == manager.id:
        return
    if open_assignment and open_assignment.valid_from < valid_from:
        open_assignment.valid_to = valid_from - timedelta(days=1)
        open_assignment.save(update_fields=['valid_to', 'updated_at'])
    ClientManagerAssignment.objects.get_or_create(
        client=client,
        manager=manager,
        valid_from=valid_from,
        defaults={'valid_to': None},
    )


def _looks_like_monthly_portfolio(sheet_name: str, frame: pd.DataFrame) -> bool:
    lowered = sheet_name.lower()
    if 'портфель' not in lowered and 'месяц' not in lowered:
        return False
    return len(frame.columns) >= 4


def _looks_like_portfolio_client_list(sheet_name: str) -> bool:
    lowered = sheet_name.lower()
    return 'список клиентов' in lowered and 'портфел' in lowered


def _looks_like_client_profile_sheet(sheet_name: str) -> bool:
    return 'портфель 2.0' in sheet_name.lower()


def _import_client_profile_sheet(batch: ImportBatch, frame: pd.DataFrame, manager_name: str) -> int:
    if len(frame) < 2:
        return 0
    headers = [_normalize_header(value) for value in frame.iloc[0].tolist()]
    if 'название' not in headers and 'название ' not in headers:
        return 0

    manager = _get_or_create_manager(manager_name)
    imported = 0
    valid_from = _earliest_period_start(frame) or date(date.today().year, 1, 1)
    title_column = _find_header_index(headers, {'название', 'название компании', 'название '})
    if title_column is None:
        return 0
    inn_column = _find_header_index(headers, {'инн'})
    internal_comment_column = _find_header_index(headers, {'комментарий мой'})
    manager_comment_column = _find_header_index(headers, {'комментарий менеджера'})

    with transaction.atomic():
        for row_index in range(1, len(frame)):
            name = _value_to_text(frame.iat[row_index, title_column])
            if not name or _is_non_client_note(name) or name.lower().startswith(('какую информацию', 'вопросы в звонке')):
                continue
            inn = _value_to_text(frame.iat[row_index, inn_column]) if inn_column is not None else ''
            if not _looks_like_inn(inn):
                inn = ''
            internal_comment = _value_to_text(frame.iat[row_index, internal_comment_column]) if internal_comment_column is not None else ''
            manager_comment = _value_to_text(frame.iat[row_index, manager_comment_column]) if manager_comment_column is not None else ''
            if row_index > 1 and not (inn or internal_comment or manager_comment):
                continue
            client, _ = Client.objects.get_or_create(name=name, defaults={'status': Client.Status.ACTIVE})
            updates = []
            if inn and client.inn != inn:
                client.inn = inn
                updates.append('inn')
            if internal_comment and client.internal_comment != internal_comment:
                client.internal_comment = internal_comment
                updates.append('internal_comment')
            if manager_comment and client.manager_comment != manager_comment:
                client.manager_comment = manager_comment
                updates.append('manager_comment')
            if updates:
                updates.append('updated_at')
                client.save(update_fields=updates)
            _ensure_assignment(client, manager, valid_from)
            imported += 1
    return imported


def _find_header_index(headers: list[str], candidates: set[str]) -> int | None:
    normalized = {_normalize_header(candidate) for candidate in candidates}
    for index, header in enumerate(headers):
        if header in normalized:
            return index
    return None


def _looks_like_inn(value: str) -> bool:
    if not value:
        return False
    cleaned = re.sub(r'[^0-9]', '', value)
    return 8 <= len(cleaned) <= 12


def _import_portfolio_client_list(batch: ImportBatch, frame: pd.DataFrame, manager_name: str) -> int:
    manager = _get_or_create_manager(manager_name)
    header_row = _find_manager_row(frame, manager.full_name)
    if header_row is None:
        header_row = 3
    client_column = 0
    valid_from = _earliest_period_start(frame) or date(date.today().year, 1, 1)
    imported = 0
    seen = set()

    batch.rows_total += max(len(frame) - header_row - 1, 0)
    batch.save(update_fields=['rows_total', 'updated_at'])

    with transaction.atomic():
        for row_index in range(header_row + 1, len(frame)):
            raw_name = _value_to_text(frame.iloc[row_index, client_column])
            lowered = raw_name.lower()
            if not raw_name:
                continue
            if 'клиенты, которых убрали' in lowered:
                break
            if _is_non_client_note(raw_name):
                continue
            if raw_name in seen:
                continue
            seen.add(raw_name)
            client, _ = Client.objects.get_or_create(name=raw_name, defaults={'status': Client.Status.ACTIVE})
            _ensure_assignment(client, manager, valid_from)
            imported += 1
    return imported


def _find_manager_row(frame: pd.DataFrame, manager_name: str) -> int | None:
    for row_index in range(min(20, len(frame))):
        values = [_value_to_text(value) for value in frame.iloc[row_index].tolist()]
        if manager_name in values:
            return row_index
    return None


def _earliest_period_start(frame: pd.DataFrame) -> date | None:
    starts = []
    for row_index in range(min(5, len(frame))):
        for value in frame.iloc[row_index].tolist():
            parsed = _parse_month_header(value)
            if parsed:
                starts.append(date(parsed[0], parsed[1], 1))
    return min(starts) if starts else None


def _is_non_client_note(text: str) -> bool:
    lowered = text.lower().strip()
    blocked_prefixes = (
        'это ',
        'одна ',
        'ошибка',
        'тоже ',
        'его нет',
        'восстановили',
        'весь портфель',
        'старый ',
        'новый',
    )
    if lowered.startswith(blocked_prefixes):
        return True
    if 'http://' in lowered or 'https://' in lowered:
        return True
    return False


def _import_monthly_portfolio_sheet(batch: ImportBatch, frame: pd.DataFrame, manager_name: str) -> int:
    imported = 0
    manager = _get_or_create_manager(manager_name or _infer_manager_name(frame))
    month_columns = []
    header_row = 0
    for candidate_row in range(min(5, len(frame))):
        row_months = []
        for column_index, value in enumerate(frame.iloc[candidate_row].tolist()):
            parsed = _parse_month_header(value)
            if parsed:
                row_months.append((column_index, parsed[0], parsed[1]))
        if len(row_months) > len(month_columns):
            month_columns = row_months
            header_row = candidate_row

    if not month_columns:
        return 0

    client_column = _detect_client_name_column(frame, header_row, [column for column, _, _ in month_columns])
    batch.rows_total += len(frame)
    batch.save(update_fields=['rows_total', 'updated_at'])

    with transaction.atomic():
        for row_index in range(header_row + 1, len(frame)):
            row = frame.iloc[row_index]
            client_name = _value_to_text(row.iloc[client_column])
            lowered_client = client_name.lower()
            if (
                not client_name
                or lowered_client in {'nan', 'итого'}
                or lowered_client.startswith(('заполнена', 'старый ', 'новый'))
                or len(client_name) < 3
            ):
                continue
            client, _ = Client.objects.get_or_create(name=client_name, defaults={'status': Client.Status.ACTIVE})
            for column, year, month in month_columns:
                revenue = _parse_decimal(_value_to_text(row.iloc[column]))
                if revenue is None or revenue <= 0:
                    continue
                period_start = date(year, month, 1)
                period_end = _month_end(period_start)
                _ensure_assignment(client, manager, period_start)
                ClientPeriodFact.objects.update_or_create(
                    client=client,
                    manager=manager,
                    period_start=period_start,
                    period_end=period_end,
                    defaults={
                        'revenue': revenue,
                        'orders_count': 1,
                        'source_batch': batch,
                    },
                )
                imported += 1
    return imported


def _detect_client_name_column(frame: pd.DataFrame, header_row: int, excluded_columns: list[int]) -> int:
    best_column = 1 if len(frame.columns) > 1 else 0
    best_score = -1
    for column_index in range(len(frame.columns)):
        if column_index in excluded_columns:
            continue
        score = 0
        for row_index in range(header_row + 1, min(len(frame), header_row + 80)):
            text = _value_to_text(frame.iloc[row_index, column_index])
            if len(text) >= 3 and not _parse_decimal(text):
                score += 1
        if score > best_score:
            best_score = score
            best_column = column_index
    return best_column


def _infer_manager_name(frame: pd.DataFrame) -> str:
    for value in frame.head(10).to_numpy().flatten():
        text = str(value).strip()
        if len(text.split()) >= 3 and not text.lower().startswith(('ано ', 'ао ', 'ооо ', 'ип ')):
            return text
    return 'Не указан'


def _parse_month_header(value: Any) -> tuple[int, int] | None:
    text = _normalize_header(value)
    if 'комментарий' in text or 'итого' in text:
        return None
    match = re.search(r'([а-я]+)\s*(20)?(\d{2})?', text)
    if not match:
        return None
    month = MONTHS.get(match.group(1))
    if not month:
        return None
    year_suffix = match.group(3)
    year = 2025
    if year_suffix:
        year = 2000 + int(year_suffix)
    elif month in {10, 11, 12}:
        year = 2024
    return year, month


def _month_end(period_start: date) -> date:
    if period_start.month == 12:
        next_month = date(period_start.year + 1, 1, 1)
    else:
        next_month = date(period_start.year, period_start.month + 1, 1)
    return next_month - timedelta(days=1)


def _normalize_header(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value).strip().lower())


def _cell(row: pd.Series, column: Any) -> str:
    if not column or column not in row:
        return ''
    return _value_to_text(row[column])


def _value_to_text(value: Any) -> str:
    if pd.isna(value):
        return ''
    return str(value).strip()


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    parsed = pd.to_datetime(value, dayfirst=True, errors='coerce')
    if pd.isna(parsed):
        return None
    return parsed.date()


def _parse_decimal(value: str) -> Decimal | None:
    if value is None or value == '':
        return None
    cleaned = str(value)
    cleaned = cleaned.replace('\xa0', '').replace(' ', '').replace('₽', '').replace(',', '.')
    cleaned = re.sub(r'[^0-9.\-]', '', cleaned)
    if cleaned in {'', '-', '.', '-.'}:
        return None
    try:
        return Decimal(cleaned).quantize(Decimal('0.01'))
    except InvalidOperation:
        return None


def _parse_int(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(float(str(value).replace(',', '.').strip()))
    except ValueError:
        return None


def _normalize_order_status(value: str) -> str:
    lowered = str(value).strip().lower()
    if lowered == 'отказ':
        return Order.Status.CANCELLED
    if 'после изготов' in lowered:
        return Order.Status.CANCELLED_AFTER_PRODUCTION
    return Order.Status.COMPLETED
