from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

from portfolio.models import Category, Client, ClientManagerAssignment, Manager, Order


class Command(BaseCommand):
    help = 'Creates demo owner, manager, clients, assignments, and orders.'

    def handle(self, *args, **options):
        owner_group, _ = Group.objects.get_or_create(name='owner')
        owner, _ = User.objects.get_or_create(username='owner', defaults={'is_staff': True})
        owner.is_staff = True
        owner.set_password('owner123')
        owner.save()
        owner.groups.add(owner_group)

        manager_user, _ = User.objects.get_or_create(username='maria')
        manager_user.set_password('maria123')
        manager_user.save()

        manager, _ = Manager.objects.get_or_create(
            user=manager_user,
            defaults={'full_name': 'Горбикова Мария Викторовна', 'email': '', 'current_year_plan': Decimal('25000000.00')},
        )
        manager.current_year_plan = Decimal('25000000.00')
        manager.save(update_fields=['current_year_plan', 'updated_at'])

        category, _ = Category.objects.get_or_create(name='Полиграфия')
        clients = [
            ('АНО ДПО "ОТКРЫТЫЙ ЦЕНТР ОБУЧЕНИЯ И РАЗВИТИЯ"', Decimal('32447.26'), Decimal('25540.75')),
            ('АО "ВНИТИ"', Decimal('24649.20'), Decimal('1015.20')),
            ('АО "ЭЙЧ ЭНД ЭН"', Decimal('148930.26'), Decimal('4764.60')),
        ]
        for name, jan_amount, feb_amount in clients:
            client, _ = Client.objects.get_or_create(name=name)
            ClientManagerAssignment.objects.get_or_create(
                client=client,
                manager=manager,
                valid_from=date(2026, 1, 1),
            )
            Order.objects.update_or_create(
                client=client,
                order_date=date(2026, 1, 15),
                defaults={
                    'amount': jan_amount,
                    'category': category,
                    'status': Order.Status.COMPLETED,
                    'cost_amount': jan_amount * Decimal('0.55'),
                    'nomenclature_group': 'Печатная продукция',
                },
            )
            Order.objects.update_or_create(
                client=client,
                order_date=date(2026, 2, 15),
                defaults={
                    'amount': feb_amount,
                    'category': category,
                    'status': Order.Status.COMPLETED,
                    'cost_amount': feb_amount * Decimal('0.55'),
                    'nomenclature_group': 'Печатная продукция',
                },
            )

        self.stdout.write(self.style.SUCCESS('Demo data created. Users: owner/owner123, maria/maria123'))
