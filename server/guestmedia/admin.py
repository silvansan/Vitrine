"""Raw editing in Django admin. Day-to-day work happens in /manage/."""
from django.contrib import admin

from .models import Agent, Guest, Marker, MediaAsset, MediaSet, ScreenGrant, Service, SetItem


class GuestInline(admin.TabularInline):
    model = Guest
    extra = 0
    fields = ["name", "token", "can_upload", "can_browse", "expires_at"]
    readonly_fields = ["token"]


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ["name", "date", "is_active", "auto_approve"]
    list_filter = ["is_active"]
    inlines = [GuestInline]


class MarkerInline(admin.TabularInline):
    model = Marker
    extra = 0


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = ["id", "original_name", "owner_name", "kind", "status", "approval", "private", "created", "deleted_at"]
    list_filter = ["kind", "status", "approval", "private"]
    search_fields = ["original_name", "owner_name"]
    inlines = [MarkerInline]

    def get_queryset(self, request):
        return MediaAsset.all_objects.all()


class SetItemInline(admin.TabularInline):
    model = SetItem
    extra = 0
    raw_id_fields = ["asset"]


@admin.register(MediaSet)
class MediaSetAdmin(admin.ModelAdmin):
    list_display = ["title", "service", "guest"]
    inlines = [SetItemInline]


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ["name", "token_prefix", "lan_ip", "last_seen", "version", "manifest_version"]
    readonly_fields = ["token_prefix", "last_seen", "lan_ip", "version", "status", "manifest_version"]


@admin.register(ScreenGrant)
class ScreenGrantAdmin(admin.ModelAdmin):
    list_display = ["name", "role", "agent", "created", "expires_at", "revoked"]
