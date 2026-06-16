import random
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import models
from django.utils import timezone

from portfolio.models import Category, Client, ClientManagerAssignment, ClientPeriodFact, Manager, Order, OrderStage


FIRST_NAMES = ['Алексей', 'Мария', 'Екатерина', 'Дмитрий', 'Ирина', 'Павел', 'Ольга', 'Сергей', 'Анна', 'Никита']
LAST_NAMES = ['Смирнов', 'Кузнецова', 'Попова', 'Волков', 'Соколова', 'Морозов', 'Новикова', 'Федоров', 'Орлова', 'Павлов']
MIDDLE_NAMES = ['Андреевич', 'Викторовна', 'Сергеевна', 'Олегович', 'Игоревна', 'Петрович', 'Александровна', 'Денисович', 'Романовна', 'Максимович']
STAGES = ['Новый', 'КП отправлено', 'Согласование', 'Договор', 'Предоплата', 'В производстве', 'Готов к отгрузке', 'Отгружен', 'Закрыт']
DEMO_START = date(2025, 7, 1)


class Command(BaseCommand):
    help = 'Prepares a realistic demo database for diploma presentation.'

    def handle(self, *args, **options):
        random.seed(42)
        managers = self._ensure_managers()
        self._fill_blank_fields()
        self._redistribute_clients(managers)
        new_clients = self._create_extra_clients(managers)
        self._create_demo_orders(managers, new_clients)
        self._normalize_order_assignments(managers)
        self.stdout.write(self.style.SUCCESS('Demo database prepared: fields filled, managers/clients/orders/stages created.'))

    def _ensure_managers(self):
        managers = []
        for index in range(10):
            full_name = f'{LAST_NAMES[index]} {FIRST_NAMES[index]} {MIDDLE_NAMES[index]}'
            email = f'manager{index + 1}@example.com'
            user, _ = User.objects.get_or_create(username=email, defaults={'email': email, 'first_name': full_name})
            user.email = email
            user.first_name = full_name
            user.set_password('manager123')
            user.save()
            manager, _ = Manager.objects.update_or_create(
                email=email,
                defaults={
                    'user': user,
                    'full_name': full_name,
                    'phone': self._phone(index),
                    'age': 25 + index * 3,
                    'current_year_plan': Decimal(8000000 + index * 750000),
                    'is_active': True,
                },
            )
            managers.append(manager)
        return managers

    def _fill_blank_fields(self):
        for index, manager in enumerate(Manager.objects.order_by('id'), start=1):
            changed = []
            if not manager.phone:
                manager.phone = self._phone(index + 5)
                changed.append('phone')
            if not manager.email:
                manager.email = f'manager-existing-{manager.id}@example.com'
                changed.append('email')
            if manager.age is None:
                manager.age = 30 + (index % 20)
                changed.append('age')
            if changed:
                manager.save(update_fields=changed + ['updated_at'])

        for index, client in enumerate(Client.objects.order_by('id'), start=1):
            changed = []
            if not client.phone:
                client.phone = self._phone(index + 20)
                changed.append('phone')
            if not client.email:
                client.email = f'{self._slug(client.name)}@example.com'
                changed.append('email')
            if not client.address:
                client.address = f'г. Москва, ул. Примерная, д. {index}'
                changed.append('address')
            if changed:
                client.save(update_fields=changed + ['updated_at'])

        for index, order in enumerate(Order.objects.order_by('id'), start=1):
            changed = []
            if not order.nomenclature_group:
                order.nomenclature_group = 'Услуги' if index % 2 else 'Товары'
                changed.append('nomenclature_group')
            if order.positions_count is None:
                order.positions_count = random.randint(1, 12)
                changed.append('positions_count')
            if changed:
                order.save(update_fields=changed + ['updated_at'])

    def _redistribute_clients(self, managers):
        clients = list(Client.objects.order_by('id'))
        weights = [30, 24, 18, 14, 10, 8, 6, 5, 4, 3]
        cursor = 0
        for manager, count in zip(managers, weights):
            for client in clients[cursor:cursor + count]:
                self._ensure_assignment(client, manager, self._client_assignment_date(client, cursor))
            cursor += count
        for index, client in enumerate(clients[cursor:]):
            self._ensure_assignment(client, managers[index % len(managers)], self._client_assignment_date(client, index))

    def _create_extra_clients(self, managers):
        names = [
            'ООО "Северный Проект"',
            'АО "Городские Решения"',
            'ООО "Линия Печати"',
            'ИП Крылова Наталья Сергеевна',
            'ООО "Бизнес Пак"',
            'АО "ТехноСервис"',
            'ООО "Медиа Контур"',
            'ООО "Промо Фокус"',
        ]
        clients = []
        for index, name in enumerate(names, start=1):
            inn = f'77{index:08d}'
            client, _ = Client.objects.update_or_create(
                inn=inn,
                defaults={
                    'name': name,
                    'address': f'г. Москва, ул. Деловая, д. {index}',
                    'phone': self._phone(index + 80),
                    'email': f'{self._slug(name)}@example.com',
                    'sales_channel': random.choice(['CRM', 'Рекомендация', 'Повторные продажи', 'Сайт']),
                    'status': Client.Status.ACTIVE,
                },
            )
            self._ensure_assignment(client, managers[index % len(managers)], self._shift_month(DEMO_START, index - 1))
            Client.objects.filter(pk=client.pk).update(created_at=self._dt(self._shift_month(DEMO_START, index - 1), 9), updated_at=self._dt(self._shift_month(DEMO_START, index), 10))
            clients.append(client)
        return clients

    def _create_demo_orders(self, managers, new_clients):
        category, _ = Category.objects.get_or_create(name='Демо-услуги')
        clients_by_manager = {
            manager.id: list(Client.objects.filter(manager_assignments__manager=manager).distinct()[:6])
            for manager in managers
        }
        timeline_start = date(2025, 7, 15)
        for manager_index, manager in enumerate(managers):
            selected_clients = clients_by_manager.get(manager.id, [])[:3]
            if not selected_clients:
                selected_clients = new_clients[:1]
            for client_index, client in enumerate(selected_clients):
                orders_to_make = 2 if client not in new_clients else random.randint(2, 5)
                for order_index in range(orders_to_make):
                    month_offset = (manager_index * 2 + client_index + order_index) % 12
                    month = self._shift_month(timeline_start, month_offset)
                    order_date = month.replace(day=min(8 + order_index * 4 + client_index, 24))
                    amount = Decimal(random.randint(35000, 450000))
                    order, _ = Order.objects.update_or_create(
                        external_order_id=f'DEMO-{manager.id}-{client.id}-{order_index}',
                        defaults={
                            'client': client,
                            'order_date': order_date,
                            'amount': amount,
                            'cost_amount': amount * Decimal('0.58'),
                            'positions_count': random.randint(1, 10),
                            'nomenclature_group': random.choice(['Услуги', 'Товары', 'Полиграфия', 'Монтаж']),
                            'category': category,
                            'status': Order.Status.COMPLETED,
                            'note': 'Демо-заказ для защиты диплома',
                        },
                    )
                    created_at = self._dt(order_date - timedelta(days=random.randint(2, 10)), 9 + order_index)
                    updated_at = self._dt(order_date + timedelta(days=random.randint(1, 12)), 13)
                    Order.objects.filter(pk=order.pk).update(created_at=created_at, updated_at=updated_at)
                    if order_index == 0:
                        target_stage = random.choice(['Новый', 'КП отправлено', 'Согласование'])
                    elif order_index == 1:
                        target_stage = random.choice(['Согласование', 'В производстве'])
                    else:
                        target_stage = random.choice(['Отгружен', 'Закрыт'])
                    self._set_order_stages(order, target_stage, created_at.date())
                    ClientPeriodFact.objects.update_or_create(
                        client=client,
                        manager=manager,
                        period_start=order_date.replace(day=1),
                        period_end=self._month_end(order_date),
                        defaults={'revenue': amount, 'orders_count': 1},
                    )

    def _set_order_stages(self, order, current_stage, started_at):
        OrderStage.objects.filter(order=order).delete()
        current_index = STAGES.index(current_stage)
        for index, title in enumerate(STAGES):
            done = index <= current_index
            stage_date = started_at + timedelta(days=index * 2)
            stage = OrderStage.objects.create(
                order=order,
                title=title,
                position=index + 1,
                is_done=done,
                completed_at=self._dt(stage_date, 11) if done else None,
            )
            OrderStage.objects.filter(pk=stage.pk).update(created_at=self._dt(stage_date, 10), updated_at=self._dt(stage_date, 12))

    def _normalize_order_assignments(self, managers):
        for index, client in enumerate(Client.objects.filter(orders__isnull=False).distinct().order_by('id')):
            first_order = client.orders.order_by('order_date').first()
            if not first_order:
                continue
            as_of = first_order.order_date
            has_assignment = ClientManagerAssignment.objects.filter(
                client=client,
                valid_from__lte=as_of,
            ).filter(models.Q(valid_to__isnull=True) | models.Q(valid_to__gte=as_of)).exists()
            if has_assignment:
                continue
            self._ensure_assignment(client, managers[index % len(managers)], max(DEMO_START, as_of - timedelta(days=20)))

    def _ensure_assignment(self, client, manager, valid_from):
        ClientManagerAssignment.objects.get_or_create(
            client=client,
            manager=manager,
            valid_from=valid_from,
            defaults={'valid_to': None},
        )

    def _client_assignment_date(self, client, seed):
        first_order = client.orders.order_by('order_date').first()
        if first_order:
            return max(DEMO_START, first_order.order_date - timedelta(days=20))
        return self._shift_month(DEMO_START, seed % 8)

    def _dt(self, value, hour):
        return timezone.make_aware(datetime.combine(value, time(hour=hour, minute=random.randint(0, 50))))

    def _phone(self, seed):
        return f'+7 9{seed % 90 + 10:02d} {seed % 900 + 100:03d}-{seed % 90 + 10:02d}-{seed % 80 + 10:02d}'

    def _slug(self, value):
        value = re.sub(r'[^0-9A-Za-zА-Яа-яЁё]+', '-', value).strip('-').lower()
        translit = {
            'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i',
            'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't',
            'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '',
            'э': 'e', 'ю': 'yu', 'я': 'ya',
        }
        result = ''.join(translit.get(char, char) for char in value)
        result = re.sub(r'[^0-9a-z-]+', '', result).strip('-')
        return result[:40] or 'client'

    def _shift_month(self, value, offset):
        month_index = value.year * 12 + value.month - 1 + offset
        year, month_zero = divmod(month_index, 12)
        return date(year, month_zero + 1, min(value.day, 28))

    def _month_end(self, value):
        next_month = self._shift_month(value.replace(day=1), 1)
        return next_month - timedelta(days=1)
