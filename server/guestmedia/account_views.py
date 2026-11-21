"""Staff accounts in /manage/: everyone edits their own account; superusers manage the team."""
import secrets

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .models import Organization
from .orgs import org_view

User = get_user_model()


def _new_password():
    return "-".join(secrets.token_urlsafe(4) for _ in range(3))


def _password_problems(password, user=None):
    try:
        validate_password(password, user)
    except ValidationError as exc:
        return " ".join(exc.messages)
    return ""


@org_view
def account(request):
    user = request.user
    password_form = PasswordChangeForm(user)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "profile":
            user.first_name = request.POST.get("first_name", "").strip()[:150]
            user.last_name = request.POST.get("last_name", "").strip()[:150]
            user.email = request.POST.get("email", "").strip()[:254]
            user.save(update_fields=["first_name", "last_name", "email"])
            messages.success(request, "Saved.")
            return redirect("manage-account")
        if action == "password":
            password_form = PasswordChangeForm(user, request.POST)
            if password_form.is_valid():
                password_form.save()
                update_session_auth_hash(request, password_form.user)  # stay signed in here
                messages.success(request, "Password changed.")
                return redirect("manage-account")
            messages.error(request, "The password wasn't changed: see below.")
    return render(request, "guestmedia/manage/account.html", {"password_form": password_form})


def _superuser_only(request):
    if not request.user.is_superuser:
        raise Http404()


def _superuser_count():
    return User.objects.filter(is_superuser=True, is_active=True).count()


@org_view
def team(request):
    """All staff accounts (superusers)."""
    _superuser_only(request)
    if request.method == "POST":
        username = request.POST.get("username", "").strip()[:150]
        email = request.POST.get("email", "").strip()[:254]
        password = request.POST.get("password", "")
        if not username:
            messages.error(request, "Choose a user name.")
            return redirect("manage-team")
        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, f"{username} already exists.")
            return redirect("manage-team")
        generated = not password
        password = password or _new_password()
        problem = "" if generated else _password_problems(password)
        if problem:
            messages.error(request, f"Password: {problem}")
            return redirect("manage-team")
        user = User.objects.create_user(username=username, email=email, password=password, is_staff=True)
        user.first_name = request.POST.get("first_name", "").strip()[:150]
        user.is_superuser = request.POST.get("is_superuser") == "on"
        user.save()
        ids = [int(i) for i in request.POST.getlist("orgs") if i.isdigit()]
        for org in Organization.objects.filter(pk__in=ids):
            org.staff.add(user)
        if generated:
            request.session["vitrine_new_password"] = {"username": username, "password": password}
        messages.success(request, f"Account {username} created.")
        return redirect("manage-team-member", user.pk)
    users = User.objects.filter(is_staff=True).prefetch_related("vitrine_orgs").order_by("-is_active", "username")
    return render(request, "guestmedia/manage/team.html", {
        "users": users, "organizations": Organization.objects.all(),
    })


@org_view
def team_member(request, pk):
    _superuser_only(request)
    member = get_object_or_404(User, pk=pk, is_staff=True)
    is_me = member.pk == request.user.pk
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update":
            member.first_name = request.POST.get("first_name", "").strip()[:150]
            member.last_name = request.POST.get("last_name", "").strip()[:150]
            member.email = request.POST.get("email", "").strip()[:254]
            make_super = request.POST.get("is_superuser") == "on"
            active = request.POST.get("is_active") == "on"
            if is_me and (not make_super or not active):
                messages.error(request, "You can't remove your own superuser rights or deactivate yourself.")
                make_super, active = member.is_superuser, member.is_active
            member.is_superuser = make_super
            member.is_active = active
            member.save()
            ids = [int(i) for i in request.POST.getlist("orgs") if i.isdigit()]
            member.vitrine_orgs.set(Organization.objects.filter(pk__in=ids))
            messages.success(request, "Saved.")
        elif action == "password":
            password = request.POST.get("password", "")
            generated = not password
            password = password or _new_password()
            problem = "" if generated else _password_problems(password, member)
            if problem:
                messages.error(request, f"Password: {problem}")
            else:
                member.set_password(password)
                member.save(update_fields=["password"])  # signs them out everywhere
                if is_me:
                    update_session_auth_hash(request, member)
                if generated:
                    request.session["vitrine_new_password"] = {"username": member.username, "password": password}
                messages.success(request, f"New password set for {member.username}. They are signed out everywhere.")
        elif action == "delete":
            if is_me:
                messages.error(request, "You can't delete your own account.")
            elif member.is_superuser and _superuser_count() <= 1:
                messages.error(request, "This is the last superuser.")
            else:
                name = member.username
                member.delete()
                messages.success(request, f"Account {name} deleted.")
                return redirect("manage-team")
        return redirect("manage-team-member", member.pk)
    return render(request, "guestmedia/manage/team_member.html", {
        "member": member,
        "is_me": is_me,
        "organizations": Organization.objects.all(),
        "member_org_ids": set(member.vitrine_orgs.values_list("pk", flat=True)),
        "new_password": request.session.pop("vitrine_new_password", None),
        "login_url": request.build_absolute_uri(reverse("login")),
    })
