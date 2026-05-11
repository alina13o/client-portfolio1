from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
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
from portfolio.services.importer import import_excel_workbook


class Command(BaseCommand):
    help = 'Deletes portfolio data and imports a workbook from scratch without old duplicates.'

    def add_arguments(self, parser):
        parser.add_argument('xlsx_path')
        parser.add_argument('--user', default='owner')

    def handle(self, *args, **options):
        path = Path(options['xlsx_path'])
        if not path.exists():
            raise CommandError(f'File not found: {path}')

        user = User.objects.filter(username=options['user']).first()

        with transaction.atomic():
            StagingOrder.objects.all().delete()
            Order.objects.all().delete()
            ClientPeriodFact.objects.all().delete()
            ClientManagerAssignment.objects.all().delete()
            ImportBatch.objects.all().delete()
            Client.objects.all().delete()
            Category.objects.all().delete()
            Manager.objects.exclude(user__username='owner').delete()

        with path.open('rb') as handle:
            result = import_excel_workbook(handle, user=user)

        maria_user = User.objects.filter(username='maria').first()
        maria_manager = Manager.objects.filter(full_name='Горбикова Мария Викторовна').first()
        if maria_user and maria_manager:
            maria_manager.user = maria_user
            maria_manager.email = maria_manager.email or maria_user.email
            maria_manager.save(update_fields=['user', 'email', 'updated_at'])

        self.stdout.write(
            self.style.SUCCESS(
                f'Imported batch={result.batch.id}: '
                f'clients={result.imported_clients}, '
                f'profiles={result.batch.errors.get("summary", {}).get("client_profiles_imported_or_updated", 0)}, '
                f'orders={result.imported_orders}, '
                f'period_facts={result.imported_period_facts}, '
                f'failed={result.failed_rows}.'
            )
        )
