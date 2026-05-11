from decimal import Decimal

from django import template


register = template.Library()


@register.filter
def percent(value):
    if value is None:
        return 'нет данных'
    try:
        return f'{Decimal(value) * Decimal("100"):.1f}%'
    except Exception:
        return value


@register.filter
def money(value):
    if value is None:
        value = Decimal('0')
    try:
        return f'{Decimal(value):,.2f}'.replace(',', ' ')
    except Exception:
        return value


@register.filter
def subtract(value, arg):
    try:
        return Decimal(value or 0) - Decimal(arg or 0)
    except Exception:
        return ''
