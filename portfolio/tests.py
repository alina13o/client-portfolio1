from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
import pandas as pd

from portfolio.models import Client, ClientManagerAssignment, Manager, Order
from portfolio.services.access import visible_clients_for_user
from portfolio.services.analytics import Period, compare_periods
from portfolio.services.importer import import_excel_workbook


class PortfolioAccessTests(TestCase):
    def test_manager_sees_only_assigned_clients(self):
        user = User.objects.create_user(username='manager', password='secret')
        manager = Manager.objects.create(user=user, full_name='Менеджер')
        visible_client = Client.objects.create(name='Видимый клиент')
        hidden_client = Client.objects.create(name='Чужой клиент')
        ClientManagerAssignment.objects.create(
            client=visible_client,
            manager=manager,
            valid_from=date(2026, 1, 1),
        )

        clients = visible_clients_for_user(user, as_of=date(2026, 2, 1))

        self.assertIn(visible_client, clients)
        self.assertNotIn(hidden_client, clients)


class PeriodComparisonTests(TestCase):
    def test_compare_periods_returns_recommendation_for_churned_client(self):
        user = User.objects.create_user(username='owner', password='secret', is_staff=True)
        manager = Manager.objects.create(full_name='Менеджер')
        client = Client.objects.create(name='ООО Тест')
        ClientManagerAssignment.objects.create(
            client=client,
            manager=manager,
            valid_from=date(2026, 1, 1),
        )
        Order.objects.create(
            client=client,
            order_date=date(2026, 1, 10),
            amount=Decimal('10000.00'),
            status=Order.Status.COMPLETED,
        )

        result = compare_periods(
            user,
            Period(date(2026, 1, 1), date(2026, 1, 31)),
            Period(date(2026, 2, 1), date(2026, 2, 28)),
        )

        self.assertEqual(result['summary']['revenue_p1'], Decimal('10000.00'))
        self.assertEqual(result['summary']['revenue_p2'], Decimal('0.00'))
        self.assertEqual(result['rows'][0]['segment'], 'Отток')


class ExcelImportTests(TestCase):
    def test_import_order_excel_normalizes_core_entities(self):
        user = User.objects.create_user(username='owner', password='secret', is_staff=True)
        buffer = BytesIO()
        pd.DataFrame(
            [
                {
                    'Менеджер': 'Горбикова Мария Викторовна',
                    'Код клиента': 'C-001',
                    'Название клиента': 'ООО Импорт',
                    'Дата заказа': '10.02.2026',
                    'Сумма заказа': '12 345,67 ₽',
                    'Количество позиций': 3,
                    'Категория товара/услуги': 'Полиграфия',
                    'Статус клиента': 'активен',
                    'Примечание': 'тест',
                }
            ]
        ).to_excel(buffer, index=False)
        buffer.seek(0)
        uploaded = SimpleUploadedFile(
            'orders.xlsx',
            buffer.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

        result = import_excel_workbook(uploaded, user=user)

        self.assertEqual(result.imported_orders, 1)
        self.assertEqual(Client.objects.get(external_code='C-001').name, 'ООО Импорт')
        self.assertEqual(Order.objects.get().amount, Decimal('12345.67'))
        self.assertEqual(Manager.objects.get().full_name, 'Горбикова Мария Викторовна')
