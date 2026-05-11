from django.contrib import admin

from .models import (
    Category,
    Client,
    ClientManagerAssignment,
    ClientPeriodFact,
    ImportBatch,
    Manager,
    Order,
    StagingOrder,
)


@admin.register(Manager)
class ManagerAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'email', 'user', 'age', 'current_year_plan', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('full_name', 'email', 'user__username')


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('name', 'external_code', 'inn', 'sales_channel', 'status')
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
