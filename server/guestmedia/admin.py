"""Raw editing in Django admin. Day-to-day work happens in /manage/."""
from django.contrib import admin

from .models import (
    Agent,
    Guest,
    Location,
    Marker,
    MediaAsset,
    MediaSet,
    Organization,
    Person,
    PersonAlias,
    ScreenGrant,
    Service,
    SetItem,
)


class GuestInline(admin.TabularInline):
    model = Guest
    extra = 0
    raw_id_fields = ["person"]


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ["name", "share_all", "created"]
    filter_horizontal = ["staff", "shares_with"]


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "agent"]
    list_filter = ["organization"]


class AliasInline(admin.TabularInline):
    model = PersonAlias
    extra = 0


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "can_upload", "can_browse", "expires_at"]
    list_filter = ["organization"]
    search_fields = ["name"]
    readonly_fields = ["token"]
    inlines = [AliasInline]


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ["name", "starts_on", "ends_on", "location", "is_active", "auto_approve"]
    list_filter = ["is_active", "location"]
    inlines = [GuestInline]


class MarkerInline(admin.TabularInline):
    model = Marker
    extra = 0


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = ["id", "original_name", "owner_name", "organization", "kind", "status", "approval", "private", "shared", "created", "deleted_at"]
    list_filter = ["organization", "kind", "status", "approval", "private", "shared"]
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
