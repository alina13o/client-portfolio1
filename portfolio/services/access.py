from django.db.models import Q

from portfolio.models import Client, Manager


OWNER_GROUPS = {'owner', 'owners', 'admin', 'admins', 'владелец', 'администратор'}


def is_owner(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    return user.groups.filter(name__in=OWNER_GROUPS).exists()


def manager_for_user(user):
    if not user.is_authenticated:
        return None
    try:
        return user.manager_profile
    except Manager.DoesNotExist:
        return None


def visible_clients_for_user(user, as_of=None):
    if is_owner(user):
        return Client.objects.all()

    manager = manager_for_user(user)
    if manager is None:
        return Client.objects.none()

    assignment_filter = Q(manager_assignments__manager=manager)
    if as_of is not None:
        assignment_filter &= Q(manager_assignments__valid_from__lte=as_of)
        assignment_filter &= (
            Q(manager_assignments__valid_to__isnull=True)
            | Q(manager_assignments__valid_to__gte=as_of)
        )
    return Client.objects.filter(assignment_filter).distinct()


def clients_for_manager(manager, as_of=None):
    assignment_filter = Q(manager_assignments__manager=manager)
    if as_of is not None:
        assignment_filter &= Q(manager_assignments__valid_from__lte=as_of)
        assignment_filter &= (
            Q(manager_assignments__valid_to__isnull=True)
            | Q(manager_assignments__valid_to__gte=as_of)
        )
    return Client.objects.filter(assignment_filter).distinct()
