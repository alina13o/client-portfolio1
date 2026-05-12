import random
import re
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from portfolio.models import Category, Client, ClientManagerAssignment, ClientPeriodFact, Manager, Order, OrderStage


FIRST_NAMES = ['Алексей', 'Мария', 'Екатерина', 'Дмитрий', 'Ирина', 'Павел', 'Ольга', 'Сергей', 'Анна', 'Никита']
LAST_NAMES = ['Смирнов', 'Кузнецова', 'Попова', 'Волков', 'Соколова', 'Морозов', 'Новикова', 'Федоров', 'Орлова', 'Павлов']
MIDDLE_NAMES = ['Андреевич', 'Викторовна', 'Сергеевна', 'Олегович', 'Игоревна', 'Петрович', 'Александровна', 'Денисович', 'Романовна', 'Максимович']
STAGES = ['Новый', 'КП отправлено', 'Согласование', 'Договор', 'Предоплата', 'В производстве', 'Готов к отгрузке', 'Отгружен', 'Закрыт']


class Command(BaseCommand):
    help = 'Prepares a realistic demo database for diploma presentation.'

    def handle(self, *args, **options):
        random.seed(42)
        managers = self._ensure_managers()
        self._fill_blank_fields()
        self._redistribute_clients(managers)
        new_clients = self._create_extra_clients(managers)
        self._create_demo_orders(managers, new_clients)
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
        today = timezone.localdate()
        current_assignments = ClientManagerAssignment.objects.filter(valid_to__isnull=True)
        current_assignments.filter(valid_from__lt=today).update(valid_to=today - timedelta(days=1))
        current_assignments.filter(valid_from__gte=today).delete()
        cursor = 0
        for manager, count in zip(managers, weights):
            for client in clients[cursor:cursor + count]:
                ClientManagerAssignment.objects.update_or_create(
                    client=client,
                    manager=manager,
                    valid_from=timezone.localdate(),
                    defaults={'valid_to': None},
                )
            cursor += count
        for index, client in enumerate(clients[cursor:]):
            ClientManagerAssignment.objects.update_or_create(
                client=client,
                manager=managers[index % len(managers)],
                valid_from=timezone.localdate(),
                defaults={'valid_to': None},
            )

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
            ClientManagerAssignment.objects.update_or_create(
                client=client,
                manager=managers[index % len(managers)],
                valid_from=timezone.localdate(),
                defaults={'valid_to': None},
            )
            clients.append(client)
        return clients

    def _create_demo_orders(self, managers, new_clients):
        category, _ = Category.objects.get_or_create(name='Демо-услуги')
        clients_by_manager = {
            manager.id: list(Client.objects.filter(manager_assignments__manager=manager).distinct()[:6])
            for manager in managers
        }
        today = timezone.localdate()
        for manager_index, manager in enumerate(managers):
            selected_clients = clients_by_manager.get(manager.id, [])[:3]
            if not selected_clients:
                selected_clients = new_clients[:1]
            for client_index, client in enumerate(selected_clients):
                orders_to_make = 2 if client not in new_clients else random.randint(2, 5)
                for order_index in range(orders_to_make):
                    months_back = (manager_index + client_index + order_index) % 12
                    month = self._shift_month(today.replace(day=15), -months_back)
                    amount = Decimal(random.randint(35000, 450000))
                    order, _ = Order.objects.update_or_create(
                        external_order_id=f'DEMO-{manager.id}-{client.id}-{order_index}',
                        defaults={
                            'client': client,
                            'order_date': month,
                            'amount': amount,
                            'cost_amount': amount * Decimal('0.58'),
                            'positions_count': random.randint(1, 10),
                            'nomenclature_group': random.choice(['Услуги', 'Товары', 'Полиграфия', 'Монтаж']),
                            'category': category,
                            'status': Order.Status.COMPLETED,
                            'note': 'Демо-заказ для защиты диплома',
                        },
                    )
                    if order_index == 0:
                        target_stage = random.choice(['Новый', 'КП отправлено', 'Согласование'])
                    elif order_index == 1:
                        target_stage = random.choice(['Согласование', 'В производстве'])
                    else:
                        target_stage = random.choice(['Отгружен', 'Закрыт'])
                    self._set_order_stages(order, target_stage)
                    ClientPeriodFact.objects.update_or_create(
                        client=client,
                        manager=manager,
                        period_start=month.replace(day=1),
                        period_end=self._month_end(month),
                        defaults={'revenue': amount, 'orders_count': 1},
                    )

    def _set_order_stages(self, order, current_stage):
        OrderStage.objects.filter(order=order).delete()
        current_index = STAGES.index(current_stage)
        for index, title in enumerate(STAGES):
            done = index < current_index
            OrderStage.objects.create(
                order=order,
                title=title,
                position=index + 1,
                is_done=done,
                completed_at=timezone.now() if done else None,
            )

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
