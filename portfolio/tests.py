from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import models
from django.test import TestCase
import pandas as pd

from portfolio.models import Client, ClientManagerAssignment, ClientComment, Manager, Order, OrderComment, OrderStage
from portfolio.services.access import visible_clients_for_user
from portfolio.services.analytics import Period, compare_periods, manager_ranking
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


class CrudValidationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner', password='secret', is_staff=True, is_superuser=True)
        self.client.force_login(self.owner)

    def test_manager_age_saves_and_duplicate_email_returns_400(self):
        response = self.client.post(
            '/admin/managers/',
            {
                'full_name': 'Иванов Иван Иванович',
                'login': 'ivanov',
                'email': 'ivanov@example.com',
                'phone': '+7 900 100-10-10',
                'age': 34,
                'current_year_plan': '1000000',
                'is_active': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)
        manager = Manager.objects.get(email='ivanov@example.com')
        self.assertEqual(manager.age, 34)

        duplicate = self.client.post(
            '/admin/managers/',
            {
                'full_name': 'Дубль',
                'login': 'ivanov2',
                'email': 'ivanov@example.com',
                'phone': '+7 900 100-10-11',
                'age': 35,
                'current_year_plan': '1000000',
                'is_active': 'on',
            },
        )
        self.assertEqual(duplicate.status_code, 400)

    def test_client_duplicate_inn_returns_400(self):
        manager = Manager.objects.create(full_name='Менеджер', email='manager@example.com')
        payload = {
            'name': 'ООО Проверка',
            'inn': '7700000001',
            'address': 'г. Москва, ул. Тестовая, д. 1',
            'phone': '+7 900 100-10-12',
            'email': 'client@example.com',
            'sales_channel': 'Сайт',
            'manager': manager.id,
        }
        self.assertEqual(self.client.post('/clients/new/', payload).status_code, 302)
        payload['name'] = 'ООО Дубль'
        self.assertEqual(self.client.post('/clients/new/', payload).status_code, 400)

    def test_comment_and_stage_endpoints_save(self):
        client = Client.objects.create(name='ООО Проект', inn='7700000002', address='г. Москва')
        order = Order.objects.create(client=client, order_date=date(2026, 5, 1), amount=Decimal('10000.00'))
        stage = OrderStage.objects.create(order=order, title='Новый', position=1)

        response = self.client.post(f'/clients/{client.id}/comments/new/', {'text': 'Комментарий клиента'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ClientComment.objects.get(client=client).text, 'Комментарий клиента')

        response = self.client.post(f'/orders/{order.id}/comments/new/', {'text': 'Комментарий заказа'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(OrderComment.objects.get(order=order).text, 'Комментарий заказа')

        response = self.client.post(f'/orders/{order.id}/stages/{stage.id}/toggle/', {'is_done': 'true'})
        self.assertEqual(response.status_code, 200)
        stage.refresh_from_db()
        self.assertTrue(stage.is_done)

    def test_manager_sees_only_own_orders(self):
        manager_user = User.objects.create_user(username='own-manager', password='secret')
        other_user = User.objects.create_user(username='other-manager', password='secret')
        manager = Manager.objects.create(user=manager_user, full_name='Свой менеджер', email='own@example.com')
        other_manager = Manager.objects.create(user=other_user, full_name='Чужой менеджер', email='other@example.com')
        own_client = Client.objects.create(name='Свой клиент', inn='7700000101')
        other_client = Client.objects.create(name='Чужой клиент', inn='7700000102')
        ClientManagerAssignment.objects.create(client=own_client, manager=manager, valid_from=date(2026, 1, 1))
        ClientManagerAssignment.objects.create(client=other_client, manager=other_manager, valid_from=date(2026, 1, 1))
        own_order = Order.objects.create(client=own_client, order_date=date(2026, 5, 1), amount=Decimal('10000.00'))
        other_order = Order.objects.create(client=other_client, order_date=date(2026, 5, 1), amount=Decimal('20000.00'))

        self.client.force_login(manager_user)
        response = self.client.get('/orders/active/?status=all')

        self.assertContains(response, f'#{own_order.id}')
        self.assertNotContains(response, f'#{other_order.id}')

    def test_closed_order_remains_visible_in_all_filter(self):
        client = Client.objects.create(name='ООО Закрытый', inn='7700000103')
        order = Order.objects.create(client=client, order_date=date(2026, 5, 1), amount=Decimal('10000.00'))
        OrderStage.objects.create(order=order, title='Закрыт', position=1, is_done=True)

        response = self.client.get('/orders/active/?status=all')

        self.assertContains(response, f'#{order.id}')

    def test_order_appears_in_financial_analytics_after_closed_stage(self):
        manager_user = User.objects.create_user(username='finance-manager', password='secret')
        manager = Manager.objects.create(user=manager_user, full_name='Финансовый менеджер', email='finance@example.com')
        client = Client.objects.create(name='ООО Финансы', inn='7700000104')
        ClientManagerAssignment.objects.create(client=client, manager=manager, valid_from=date(2026, 1, 1))
        order = Order.objects.create(
            client=client,
            order_date=date(2026, 5, 1),
            amount=Decimal('120000.00'),
            cost_amount=Decimal('70000.00'),
        )
        stages = [
            OrderStage.objects.create(order=order, title='Новый', position=1),
            OrderStage.objects.create(order=order, title='Закрыт', position=2),
        ]

        self.client.force_login(manager_user)
        before = self.client.get(f'/api/dashboard/charts/?manager_id={manager.id}')
        self.assertNotIn(120000.0, before.json()['charts']['revenue'])

        response = self.client.post(f'/orders/{order.id}/stages/{stages[1].id}/toggle/', {'is_done': 'true'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['current_stage']['title'], 'Закрыт')
        self.assertFalse(payload['is_active_project'])
        after = self.client.get(f'/api/dashboard/charts/?manager_id={manager.id}')
        self.assertIn(120000.0, after.json()['charts']['revenue'])
        ranking = manager_ranking(Period(date(1900, 1, 1), date.today()))
        row = next(item for item in ranking if item['manager'].id == manager.id)
        self.assertEqual(row['revenue'], Decimal('120000.00'))
        self.assertEqual(row['profit'], Decimal('50000.00'))

    def test_dashboard_chart_api_returns_profit_details_with_order_link(self):
        manager_user = User.objects.create_user(username='detail-manager', password='secret')
        manager = Manager.objects.create(user=manager_user, full_name='Детальный менеджер', email='detail@example.com')
        client = Client.objects.create(name='ООО Детализация', inn='7700000105')
        ClientManagerAssignment.objects.create(client=client, manager=manager, valid_from=date(2026, 1, 1))
        order = Order.objects.create(
            client=client,
            order_date=date(2026, 4, 5),
            amount=Decimal('90000.00'),
            cost_amount=Decimal('55000.00'),
        )
        OrderStage.objects.create(order=order, title='Закрыт', position=1, is_done=True)

        self.client.force_login(manager_user)
        response = self.client.get(f'/api/dashboard/charts/?manager_id={manager.id}')

        self.assertEqual(response.status_code, 200)
        charts = response.json()['charts']
        self.assertIn('2026-04', charts['details'])
        detail = next(row for row in charts['details']['2026-04'] if row['order_id'] == order.id)
        self.assertEqual(detail['profit'], 35000.0)
        self.assertEqual(detail['edit_url'], f'/orders/active/?status=all#order-{order.id}')

    def test_owner_must_choose_manager_when_creating_order(self):
        client = Client.objects.create(name='ООО Без менеджера', inn='7700000106')

        response = self.client.post(
            '/orders/new/',
            {
                'client': client.id,
                'order_date': '2026-05-10',
                'amount': '50000',
                'cost_amount': '30000',
                'positions_count': '2',
                'nomenclature_group': 'Услуги',
                'status': Order.Status.COMPLETED,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Order.objects.filter(client=client).exists())

    def test_manager_order_creation_assigns_client_to_manager(self):
        manager_user = User.objects.create_user(username='auto-manager', password='secret')
        manager = Manager.objects.create(user=manager_user, full_name='Авто менеджер', email='auto@example.com')
        client = Client.objects.create(name='ООО Автоназначение', inn='7700000107')
        ClientManagerAssignment.objects.create(client=client, manager=manager, valid_from=date(2026, 1, 1))
        self.client.force_login(manager_user)

        response = self.client.post(
            '/orders/new/',
            {
                'client': client.id,
                'order_date': '2026-05-10',
                'amount': '50000',
                'cost_amount': '30000',
                'positions_count': '2',
                'nomenclature_group': 'Услуги',
                'status': Order.Status.COMPLETED,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Order.objects.filter(client=client).exists())
        self.assertTrue(ClientManagerAssignment.objects.filter(client=client, manager=manager, valid_to__isnull=True).exists())

    def test_demo_preparation_leaves_orders_with_manager_assignments(self):
        call_command('prepare_demo_database', verbosity=0)
        orders_without_manager = []
        for order in Order.objects.select_related('client').all():
            has_manager = ClientManagerAssignment.objects.filter(
                client=order.client,
                valid_from__lte=order.order_date,
            ).filter(models.Q(valid_to__isnull=True) | models.Q(valid_to__gte=order.order_date)).exists()
            if not has_manager:
                orders_without_manager.append(order.id)

        self.assertEqual(orders_without_manager, [])
        self.assertTrue(Order.objects.filter(created_at__date__lt=date(2026, 6, 16)).exists())
