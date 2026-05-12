from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Manager(TimestampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='manager_profile',
        blank=True,
        null=True,
    )
    full_name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=64, blank=True)
    age = models.PositiveIntegerField(blank=True, null=True)
    current_year_plan = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=['full_name']),
            models.Index(fields=['is_active']),
        ]
        ordering = ['full_name']

    def __str__(self):
        return self.full_name


class Client(TimestampedModel):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Активен'
        INACTIVE = 'inactive', 'Неактивен'
        ARCHIVED = 'archived', 'Архив'

    external_code = models.CharField(max_length=64, blank=True, null=True, unique=True)
    inn = models.CharField(max_length=32, blank=True)
    name = models.CharField(max_length=500)
    address = models.CharField(max_length=1000, blank=True)
    phone = models.CharField(max_length=64, blank=True)
    email = models.EmailField(blank=True)
    sales_channel = models.CharField(max_length=255, blank=True)
    internal_comment = models.TextField(blank=True)
    manager_comment = models.TextField(blank=True)
    profile_data = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['inn']),
            models.Index(fields=['status']),
        ]
        ordering = ['name']

    def __str__(self):
        return self.name


class ClientManagerAssignment(TimestampedModel):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='manager_assignments')
    manager = models.ForeignKey(Manager, on_delete=models.PROTECT, related_name='client_assignments')
    valid_from = models.DateField()
    valid_to = models.DateField(blank=True, null=True)
    note = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['client', 'manager', 'valid_from'],
                name='uniq_client_manager_assignment_start',
            ),
            models.CheckConstraint(
                condition=Q(valid_to__isnull=True) | Q(valid_to__gte=models.F('valid_from')),
                name='assignment_valid_to_after_valid_from',
            ),
        ]
        indexes = [
            models.Index(fields=['client', 'valid_from', 'valid_to']),
            models.Index(fields=['manager', 'valid_from', 'valid_to']),
        ]
        ordering = ['client__name', '-valid_from']

    def clean(self):
        if self.valid_to and self.valid_to < self.valid_from:
            raise ValidationError('Дата окончания закрепления не может быть раньше даты начала.')

    def __str__(self):
        return f'{self.client} -> {self.manager} ({self.valid_from} - {self.valid_to or "сейчас"})'


class Category(TimestampedModel):
    name = models.CharField(max_length=255, unique=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ImportBatch(TimestampedModel):
    class Status(models.TextChoices):
        UPLOADED = 'uploaded', 'Загружен'
        VALIDATED = 'validated', 'Проверен'
        IMPORTED = 'imported', 'Импортирован'
        FAILED = 'failed', 'Ошибка'

    filename = models.CharField(max_length=500)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='portfolio_imports',
        blank=True,
        null=True,
    )
    period_from = models.DateField(blank=True, null=True)
    period_to = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.UPLOADED)
    rows_total = models.PositiveIntegerField(default=0)
    rows_imported = models.PositiveIntegerField(default=0)
    rows_failed = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['status']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.filename} ({self.get_status_display()})'


class Order(TimestampedModel):
    class Status(models.TextChoices):
        COMPLETED = 'completed', 'Заказ завершен'
        CANCELLED = 'cancelled', 'Отказ'
        CANCELLED_AFTER_PRODUCTION = 'cancelled_after_production', 'Отказ после изготовления'

    external_order_id = models.CharField(max_length=128, blank=True)
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name='orders')
    order_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    cost_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    positions_count = models.PositiveIntegerField(blank=True, null=True)
    nomenclature_group = models.CharField(max_length=255, blank=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        related_name='orders',
        blank=True,
        null=True,
    )
    status = models.CharField(max_length=64, choices=Status.choices, default=Status.COMPLETED)
    note = models.TextField(blank=True)
    import_batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.SET_NULL,
        related_name='orders',
        blank=True,
        null=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=['order_date']),
            models.Index(fields=['client', 'order_date']),
            models.Index(fields=['category', 'order_date']),
            models.Index(fields=['nomenclature_group', 'order_date']),
            models.Index(fields=['status', 'order_date']),
        ]
        ordering = ['-order_date', 'client__name']

    def __str__(self):
        return f'{self.client} - {self.order_date} - {self.amount}'


class OrderStage(TimestampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='stages')
    title = models.CharField(max_length=255)
    position = models.PositiveIntegerField(default=0)
    is_done = models.BooleanField(default=False)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['order', 'position']),
            models.Index(fields=['is_done']),
        ]
        ordering = ['position', 'id']

    def __str__(self):
        return f'{self.order_id}: {self.title}'


class ClientComment(TimestampedModel):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    text = models.TextField()

    class Meta:
        indexes = [
            models.Index(fields=['client', '-created_at']),
            models.Index(fields=['author', '-created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.client_id}: {self.text[:40]}'


class OrderComment(TimestampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    text = models.TextField()

    class Meta:
        indexes = [
            models.Index(fields=['order', '-created_at']),
            models.Index(fields=['author', '-created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.order_id}: {self.text[:40]}'


class Notification(TimestampedModel):
    class Kind(models.TextChoices):
        CLIENT_COMMENT = 'client_comment', 'Комментарий к клиенту'
        ORDER_COMMENT = 'order_comment', 'Комментарий к заказу'

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='portfolio_notifications')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name='sent_portfolio_notifications')
    kind = models.CharField(max_length=32, choices=Kind.choices)
    client = models.ForeignKey(Client, on_delete=models.CASCADE, blank=True, null=True, related_name='notifications')
    order = models.ForeignKey(Order, on_delete=models.CASCADE, blank=True, null=True, related_name='notifications')
    comment_text = models.TextField(blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['recipient', 'is_read', '-created_at']),
            models.Index(fields=['kind', 'is_read']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.recipient_id}: {self.kind}'


class StagingOrder(TimestampedModel):
    import_batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name='staging_orders')
    row_number = models.PositiveIntegerField()
    manager_name = models.CharField(max_length=255, blank=True)
    client_code = models.CharField(max_length=64, blank=True)
    inn = models.CharField(max_length=32, blank=True)
    client_name = models.CharField(max_length=500, blank=True)
    address = models.CharField(max_length=1000, blank=True)
    sales_channel = models.CharField(max_length=255, blank=True)
    external_order_id = models.CharField(max_length=128, blank=True)
    order_date = models.CharField(max_length=64, blank=True)
    amount = models.CharField(max_length=64, blank=True)
    cost_amount = models.CharField(max_length=64, blank=True)
    positions_count = models.CharField(max_length=64, blank=True)
    category_name = models.CharField(max_length=255, blank=True)
    nomenclature_group = models.CharField(max_length=255, blank=True)
    client_status = models.CharField(max_length=64, blank=True)
    note = models.TextField(blank=True)
    validation_errors = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['import_batch', 'row_number'],
                name='uniq_staging_row_per_batch',
            ),
        ]
        indexes = [
            models.Index(fields=['import_batch', 'row_number']),
        ]
        ordering = ['import_batch', 'row_number']

    def __str__(self):
        return f'{self.import_batch_id}: row {self.row_number}'


class ClientPeriodFact(TimestampedModel):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='period_facts')
    manager = models.ForeignKey(
        Manager,
        on_delete=models.SET_NULL,
        related_name='period_facts',
        blank=True,
        null=True,
    )
    period_start = models.DateField()
    period_end = models.DateField()
    revenue = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    orders_count = models.PositiveIntegerField(default=0)
    source_batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.SET_NULL,
        related_name='period_facts',
        blank=True,
        null=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['client', 'manager', 'period_start', 'period_end'],
                name='uniq_client_manager_period_fact',
            ),
        ]
        indexes = [
            models.Index(fields=['period_start', 'period_end']),
            models.Index(fields=['manager', 'period_start']),
            models.Index(fields=['client', 'period_start']),
        ]

    def __str__(self):
        return f'{self.client} {self.period_start} - {self.period_end}: {self.revenue}'
