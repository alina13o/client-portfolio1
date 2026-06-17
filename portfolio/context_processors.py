from portfolio.models import Notification
from portfolio.services.access import is_owner


def notification_badges(request):
    if not request.user.is_authenticated:
        return {}
    unread = Notification.objects.filter(recipient=request.user, is_read=False)
    return {
        'current_user_is_owner': is_owner(request.user),
        'unread_notifications_count': unread.count(),
        'unread_client_comments_count': unread.filter(kind=Notification.Kind.CLIENT_COMMENT).count(),
        'unread_order_comments_count': unread.filter(kind=Notification.Kind.ORDER_COMMENT).count(),
    }
