from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

from portfolio.models import Manager


class Command(BaseCommand):
    help = 'Ensures default owner and Maria manager accounts exist without creating demo clients.'

    def handle(self, *args, **options):
        owner_group, _ = Group.objects.get_or_create(name='owner')
        owner, _ = User.objects.get_or_create(
            username='owner',
            defaults={'is_staff': True, 'is_superuser': True, 'email': 'owner@example.com'},
        )
        owner.is_staff = True
        owner.is_superuser = True
        if not owner.email:
            owner.email = 'owner@example.com'
        owner.set_password('owner123')
        owner.save()
        owner.groups.add(owner_group)

        manager_user, _ = User.objects.get_or_create(
            username='maria',
            defaults={'email': 'maria@example.com'},
        )
        if not manager_user.email:
            manager_user.email = 'maria@example.com'
        manager_user.set_password('maria123')
        manager_user.save()

        manager, _ = Manager.objects.get_or_create(
            user=manager_user,
            defaults={
                'full_name': 'Горбикова Мария Викторовна',
                'email': manager_user.email,
                'current_year_plan': Decimal('25000000.00'),
            },
        )
        manager.full_name = 'Горбикова Мария Викторовна'
        manager.email = manager_user.email
        manager.current_year_plan = Decimal('25000000.00')
        manager.is_active = True
        manager.save()

        self.stdout.write(self.style.SUCCESS('Default users ensured: owner/owner123, maria/maria123'))
