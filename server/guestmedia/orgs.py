"""Which organization a staff member is working in."""
from functools import wraps

from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404

from .models import Organization


def user_orgs(user):
    if user.is_superuser:
        return Organization.objects.all()
    return Organization.objects.filter(staff=user)


def current_org(request):
    orgs = user_orgs(request.user)
    if not orgs.exists() and request.user.is_superuser:
        # Fresh install: start with one organization.
        org = Organization.objects.create(name="Main")
        org.staff.add(request.user)
        orgs = user_orgs(request.user)
    wanted = request.session.get("vitrine_org")
    org = orgs.filter(pk=wanted).first() if wanted else None
    return org or orgs.first()


def org_view(view):
    """Staff only; sets request.org (current organization) and request.orgs (all of the user's)."""

    @staff_member_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        request.orgs = list(user_orgs(request.user))
        request.org = current_org(request)
        if request.org is None:
            raise Http404("Your account isn't in any organization yet. Ask an admin to add you.")
        return view(request, *args, **kwargs)

    return wrapper


def check_org(request, organization):
    """404 unless the organization is one of the user's."""
    if organization is None:
        if request.user.is_superuser:
            return
        raise Http404()
    if organization.pk not in {o.pk for o in request.orgs}:
        raise Http404()
