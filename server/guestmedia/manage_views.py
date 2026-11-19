"""Staff backend: services, guest links, approvals, library, agents, tablet pairing."""
import io
from datetime import timedelta
from urllib.parse import urlencode

import qrcode
import qrcode.image.svg
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .files import serve_field
from .models import Agent, Guest, Marker, MediaAsset, MediaSet, ScreenGrant, Service, SetItem, new_guest_token
from .notify import notify_agents
from .signing import sign_pairing
from .tasks import delete_asset_files, process_asset


class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ["name", "date", "is_active", "auto_approve"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class GuestForm(forms.ModelForm):
    class Meta:
        model = Guest
        fields = ["name", "can_upload", "can_browse", "expires_at"]
        widgets = {"expires_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}


def parse_time(text):
    """'1:12' or '72' -> milliseconds."""
    text = (text or "").strip()
    if not text:
        return 0
    parts = text.split(":")
    try:
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + float(part)
    except ValueError:
        return 0
    return int(seconds * 1000)


@staff_member_required
def dashboard(request):
    if request.method == "POST":
        form = ServiceForm(request.POST)
        if form.is_valid():
            service = form.save()
            messages.success(request, f"Created {service}.")
            return redirect("manage-service", service.pk)
    else:
        form = ServiceForm()
    services = Service.objects.annotate(guest_count=Count("guests", distinct=True))[:50]
    pending = MediaAsset.objects.filter(approval=MediaAsset.APPROVAL_PENDING).exclude(status=MediaAsset.STATUS_FAILED).count()
    return render(
        request,
        "guestmedia/manage/dashboard.html",
        {"services": services, "form": form, "pending": pending, "agents": Agent.objects.all()},
    )


@staff_member_required
def service_detail(request, pk):
    service = get_object_or_404(Service, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update":
            form = ServiceForm(request.POST, instance=service)
            if form.is_valid():
                form.save()
                messages.success(request, "Saved.")
        elif action == "add_guest":
            form = GuestForm(request.POST)
            if form.is_valid():
                guest = form.save(commit=False)
                guest.service = service
                guest.save()
                guest.get_set()
                messages.success(request, f"Link created for {guest.name}.")
        elif action == "new_link":
            guest = get_object_or_404(Guest, pk=request.POST.get("guest_id"), service=service)
            guest.token = new_guest_token()
            guest.save(update_fields=["token"])
            messages.success(request, f"New link for {guest.name}; the old one no longer works.")
        elif action == "delete_guest":
            guest = get_object_or_404(Guest, pk=request.POST.get("guest_id"), service=service)
            guest.delete()
            messages.success(request, "Guest removed. Their uploads stay in the library.")
        elif action == "rename_set":
            media_set = get_object_or_404(MediaSet, pk=request.POST.get("set_id"), service=service)
            media_set.title = request.POST.get("title", media_set.title)[:200]
            media_set.save(update_fields=["title"])
        return redirect("manage-service", service.pk)

    guests = service.guests.all()
    sets = (
        MediaSet.objects.filter(service=service)
        .select_related("guest")
        .prefetch_related("items__asset")
    )
    pending = (
        MediaAsset.objects.filter(approval=MediaAsset.APPROVAL_PENDING)
        .filter(Q(service=service) | Q(set_items__media_set__service=service))
        .exclude(status=MediaAsset.STATUS_FAILED)
        .distinct()
    )
    return render(
        request,
        "guestmedia/manage/service.html",
        {
            "service": service,
            "service_form": ServiceForm(instance=service),
            "guest_form": GuestForm(),
            "guests": guests,
            "sets": sets,
            "pending": pending,
        },
    )


@staff_member_required
@require_POST
def approve(request, pk):
    asset = get_object_or_404(MediaAsset.all_objects, pk=pk)
    decision = request.POST.get("decision")
    if decision == "approve":
        asset.approval = MediaAsset.APPROVAL_APPROVED
    elif decision == "reject":
        asset.approval = MediaAsset.APPROVAL_REJECTED
    asset.save(update_fields=["approval"])
    return redirect(request.POST.get("next") or "manage-library")


@staff_member_required
def library(request):
    show_trash = request.GET.get("trash") == "1"
    assets = MediaAsset.all_objects.all() if show_trash else MediaAsset.objects.all()
    if show_trash:
        assets = assets.filter(deleted_at__isnull=False)

    q = request.GET.get("q", "").strip()
    if q:
        assets = assets.filter(Q(original_name__icontains=q) | Q(owner_name__icontains=q))
    kind = request.GET.get("kind", "")
    if kind in ("photo", "video"):
        assets = assets.filter(kind=kind)
    approval = request.GET.get("approval", "")
    if approval in ("pending", "approved", "rejected"):
        assets = assets.filter(approval=approval)
    unused = request.GET.get("unused", "")
    if unused.isdigit():
        cutoff = timezone.now() - timedelta(days=int(unused))
        assets = assets.filter(Q(last_used__lt=cutoff) | Q(last_used__isnull=True, created__lt=cutoff))

    if request.method == "POST":
        ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
        selected = MediaAsset.all_objects.filter(pk__in=ids)
        action = request.POST.get("action")
        in_active = SetItem.objects.filter(asset__in=selected, media_set__service__is_active=True)
        if action == "trash":
            if in_active.exists() and request.POST.get("confirm") != "1":
                names = ", ".join(sorted({i.asset.original_name for i in in_active.select_related("asset")})[:8])
                messages.warning(
                    request,
                    f"Some files are in sets of active services ({names}). Tick 'I know' and try again to delete them anyway.",
                )
            else:
                count = selected.update(deleted_at=timezone.now())
                SetItem.objects.filter(asset__in=ids).delete()
                notify_agents()
                messages.success(request, f"Moved {count} file(s) to the trash.")
        elif action == "restore":
            selected.update(deleted_at=None)
            notify_agents()
            messages.success(request, "Restored.")
        elif action == "purge":
            for asset in selected.filter(deleted_at__isnull=False):
                delete_asset_files(asset)
                asset.delete()
            messages.success(request, "Deleted for good.")
        elif action in ("approve", "reject"):
            for asset in selected:
                asset.approval = MediaAsset.APPROVAL_APPROVED if action == "approve" else MediaAsset.APPROVAL_REJECTED
                asset.save(update_fields=["approval"])
            messages.success(request, "Updated.")
        elif action in ("private", "public"):
            for asset in selected:
                asset.private = action == "private"
                asset.save(update_fields=["private"])
        elif action == "reprocess":
            for asset in selected:
                process_asset.delay(asset.pk)
            messages.success(request, "Processing again.")
        return redirect(request.get_full_path())

    totals = assets.aggregate(size=Sum("processed_size"), count=Count("id"))
    return render(
        request,
        "guestmedia/manage/library.html",
        {
            "assets": assets.select_related("service")[:300],
            "totals": totals,
            "show_trash": show_trash,
            "filters": request.GET,
            "trash_days": settings.VITRINE_TRASH_DAYS,
        },
    )


@staff_member_required
def asset_detail(request, pk):
    asset = get_object_or_404(MediaAsset.all_objects, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update":
            asset.owner_name = request.POST.get("owner_name", asset.owner_name)[:120]
            asset.private = request.POST.get("private") == "on"
            asset.save(update_fields=["owner_name", "private"])
        elif action == "add_marker":
            label = request.POST.get("label", "").strip()[:60]
            if label:
                Marker.objects.create(
                    asset=asset, label=label, t_ms=parse_time(request.POST.get("time")), created_by="staff"
                )
        elif action == "delete_marker":
            Marker.objects.filter(asset=asset, pk=request.POST.get("marker_id")).delete()
        return redirect("manage-asset", asset.pk)
    sets = MediaSet.objects.filter(items__asset=asset).select_related("service")
    return render(request, "guestmedia/manage/asset.html", {"asset": asset, "sets": sets})


@staff_member_required
def asset_file(request, pk, variant):
    asset = get_object_or_404(MediaAsset.all_objects, pk=pk)
    field = {"processed": asset.processed, "thumb": asset.thumb, "filmstrip": asset.filmstrip, "original": asset.original}.get(variant)
    if field is None:
        raise Http404()
    return serve_field(request, field)


@staff_member_required
def agents(request):
    new_token = None
    pairing = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            name = request.POST.get("name", "").strip() or "vMix PC"
            agent, new_token = Agent.create_with_token(name)
            messages.success(request, f"Agent {agent.name} created. Copy the token now; it is shown only once.")
        elif action == "rotate":
            agent = get_object_or_404(Agent, pk=request.POST.get("agent_id"))
            new_token = agent.rotate_token()
            messages.success(request, f"New token for {agent.name}. Update the agent's config.")
        elif action == "delete":
            Agent.objects.filter(pk=request.POST.get("agent_id")).delete()
            return redirect("manage-agents")
        elif action == "pair":
            agent = get_object_or_404(Agent, pk=request.POST.get("agent_id"))
            role = request.POST.get("role") if request.POST.get("role") in ("back", "pulpit") else "pulpit"
            name = request.POST.get("name", "").strip() or ("Pulpit tablet" if role == "pulpit" else "Back tablet")
            grant = ScreenGrant.objects.create(
                agent=agent, role=role, name=name[:120], expires_at=timezone.now() + timedelta(minutes=15)
            )
            token = sign_pairing(grant)
            base = agent.tablet_base_url or "http://<vmix-pc-ip>:8090"
            pairing = {"grant": grant, "url": f"{base}/pair?{urlencode({'t': token})}", "agent": agent}
        elif action == "revoke":
            grant = get_object_or_404(ScreenGrant, pk=request.POST.get("grant_id"))
            grant.revoked = True
            grant.save(update_fields=["revoked"])
            messages.success(request, f"{grant.name} will be unpaired as soon as the agent syncs.")
            return redirect("manage-agents")
    return render(
        request,
        "guestmedia/manage/agents.html",
        {
            "agents": Agent.objects.prefetch_related("grants"),
            "new_token": new_token,
            "pairing": pairing,
        },
    )


@staff_member_required
def qr_svg(request):
    data = request.GET.get("d", "")
    if not data or len(data) > 2000:
        raise Http404()
    image = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    return HttpResponse(buffer.getvalue(), content_type="image/svg+xml")
