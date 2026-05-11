from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

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
from portfolio.services.importer import MARIA_MANAGER_NAME, import_excel_workbook


class Command(BaseCommand):
    help = 'Deletes portfolio business data and imports a clean Excel workbook once.'

    def add_arguments(self, parser):
        parser.add_argument('excel_path', type=str)
        parser.add_argument('--owner-username', default='owner')

    def handle(self, *args, **options):
        path = Path(options['excel_path'])
        if not path.exists():
            raise CommandError(f'Excel file not found: {path}')

        owner, _ = User.objects.get_or_create(username=options['owner_username'], defaults={'is_staff': True})
        owner.is_staff = True
        owner.save(update_fields=['is_staff'])

        maria_user, _ = User.objects.get_or_create(username='maria')
        maria_user.set_password('maria123')
        maria_user.save()
        manager, _ = Manager.objects.get_or_create(
            full_name=MARIA_MANAGER_NAME,
            defaults={'user': maria_user, 'current_year_plan': 25000000},
        )
        manager.user = maria_user
        manager.is_active = True
        manager.save()

        Order.objects.all().delete()
        ClientPeriodFact.objects.all().delete()
        ClientManagerAssignment.objects.all().delete()
        StagingOrder.objects.all().delete()
        ImportBatch.objects.all().delete()
        Client.objects.all().delete()
        Category.objects.all().delete()

        with path.open('rb') as handle:
            result = import_excel_workbook(handle, user=owner)

        self.stdout.write(
            self.style.SUCCESS(
                f'Clean import done: clients={Client.objects.count()}, '
                f'facts={ClientPeriodFact.objects.count()}, rows={result.batch.rows_imported}'
            )
        )
