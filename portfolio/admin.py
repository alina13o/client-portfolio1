from django.contrib import admin

from .models import (
    Category,
    Client,
    ClientComment,
    ClientManagerAssignment,
    ClientPeriodFact,
    ImportBatch,
    Manager,
    Notification,
    Order,
    OrderComment,
    OrderStage,
    StagingOrder,
)


@admin.register(Manager)
class ManagerAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'email', 'phone', 'user', 'age', 'current_year_plan', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('full_name', 'email', 'user__username')


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('name', 'external_code', 'inn', 'phone', 'email', 'sales_channel', 'status')
    list_filter = ('status',)
    search_fields = ('name', 'external_code', 'inn', 'address', 'internal_comment', 'manager_comment')


@admin.register(ClientManagerAssignment)
class ClientManagerAssignmentAdmin(admin.ModelAdmin):
    list_display = ('client', 'manager', 'valid_from', 'valid_to')
    list_filter = ('manager', 'valid_from')
    search_fields = ('client__name', 'manager__full_name')


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    search_fields = ('name',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('client', 'order_date', 'amount', 'cost_amount', 'positions_count', 'category', 'nomenclature_group', 'status')
    list_filter = ('status', 'category', 'nomenclature_group', 'order_date')
    search_fields = ('client__name', 'external_order_id', 'note', 'nomenclature_group')
    date_hierarchy = 'order_date'


@admin.register(OrderStage)
class OrderStageAdmin(admin.ModelAdmin):
    list_display = ('order', 'position', 'title', 'is_done', 'completed_at')
    list_filter = ('is_done',)
    search_fields = ('order__client__name', 'title')


@admin.register(ClientComment)
class ClientCommentAdmin(admin.ModelAdmin):
    list_display = ('client', 'author', 'created_at')
    search_fields = ('client__name', 'author__username', 'text')


@admin.register(OrderComment)
class OrderCommentAdmin(admin.ModelAdmin):
    list_display = ('order', 'author', 'created_at')
    search_fields = ('order__client__name', 'author__username', 'text')


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'actor', 'kind', 'is_read', 'created_at')
    list_filter = ('kind', 'is_read')
    search_fields = ('recipient__username', 'actor__username', 'comment_text')


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ('filename', 'status', 'rows_total', 'rows_imported', 'rows_failed', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('filename',)


@admin.register(StagingOrder)
class StagingOrderAdmin(admin.ModelAdmin):
    list_display = ('import_batch', 'row_number', 'client_name', 'amount', 'order_date')
    list_filter = ('import_batch',)
    search_fields = ('client_name', 'manager_name')


@admin.register(ClientPeriodFact)
class ClientPeriodFactAdmin(admin.ModelAdmin):
    list_display = ('client', 'manager', 'period_start', 'period_end', 'revenue', 'orders_count')
    list_filter = ('manager', 'period_start', 'period_end')
    search_fields = ('client__name', 'manager__full_name')
