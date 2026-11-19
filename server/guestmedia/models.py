"""Data model.

Files live once in the library (MediaAsset). A guest's set (MediaSet) is an
ordered list that points at library files, so the same file can be in several
guests' sets without being copied. The agent on the vMix PC only downloads the
files that are in sets of the service it follows.
"""
import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


def new_guest_token():
    return secrets.token_urlsafe(18)


def new_nonce():
    return secrets.token_hex(12)


class Service(models.Model):
    name = models.CharField(max_length=200)
    date = models.DateField(default=timezone.localdate)
    is_active = models.BooleanField(
        default=True,
        help_text="Active services are sent to the agents. The agent picks one of them as its session.",
    )
    auto_approve = models.BooleanField(
        default=False,
        help_text="Skip the approval queue for uploads from this service's guests.",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.name} · {self.date:%d %b %Y}"


class Guest(models.Model):
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="guests")
    name = models.CharField(max_length=120)
    token = models.CharField(max_length=64, unique=True, default=new_guest_token)
    can_upload = models.BooleanField(default=True)
    can_browse = models.BooleanField(default=True, help_text="May pick files from the shared library.")
    expires_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.service.name})"

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at < timezone.now()

    @property
    def link(self):
        return f"{settings.VITRINE_PUBLIC_URL}/g/{self.token}/"

    def get_set(self):
        media_set, _ = MediaSet.objects.get_or_create(
            service=self.service, guest=self, defaults={"title": f"{self.name}"}
        )
        return media_set


class AliveAssetManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


def original_upload_path(instance, filename):
    return f"originals/{uuid.uuid4().hex}/{filename}"


class MediaAsset(models.Model):
    KIND_PHOTO = "photo"
    KIND_VIDEO = "video"
    KIND_CHOICES = [(KIND_PHOTO, "Photo"), (KIND_VIDEO, "Video")]

    STATUS_PROCESSING = "processing"
    STATUS_READY = "ready"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PROCESSING, "Processing"),
        (STATUS_READY, "Ready"),
        (STATUS_FAILED, "Failed"),
    ]

    APPROVAL_PENDING = "pending"
    APPROVAL_APPROVED = "approved"
    APPROVAL_REJECTED = "rejected"
    APPROVAL_CHOICES = [
        (APPROVAL_PENDING, "Waiting for approval"),
        (APPROVAL_APPROVED, "Approved"),
        (APPROVAL_REJECTED, "Rejected"),
    ]

    owner_name = models.CharField(max_length=120, help_text="Shown as 'from …' on the tablets.")
    uploaded_by = models.ForeignKey(Guest, null=True, blank=True, on_delete=models.SET_NULL, related_name="uploads")
    service = models.ForeignKey(Service, null=True, blank=True, on_delete=models.SET_NULL, related_name="uploads")
    original_name = models.CharField(max_length=255)
    original = models.FileField(upload_to=original_upload_path, max_length=500, blank=True)
    original_size = models.BigIntegerField(default=0)

    kind = models.CharField(max_length=10, choices=KIND_CHOICES, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_PROCESSING)
    error = models.TextField(blank=True)
    approval = models.CharField(max_length=10, choices=APPROVAL_CHOICES, default=APPROVAL_PENDING)
    private = models.BooleanField(default=False, help_text="Only the uploader can pick it; hidden from the shared library.")

    processed = models.FileField(max_length=500, blank=True)
    processed_size = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    thumb = models.FileField(max_length=500, blank=True)
    filmstrip = models.FileField(max_length=500, blank=True)
    filmstrip_count = models.PositiveIntegerField(default=0)
    filmstrip_cols = models.PositiveIntegerField(default=0)
    filmstrip_interval_ms = models.PositiveIntegerField(default=5000)
    duration_ms = models.PositiveIntegerField(default=0)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)

    created = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = AliveAssetManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"#{self.pk} {self.original_name} ({self.owner_name})"

    @property
    def is_playable(self):
        return (
            self.status == self.STATUS_READY
            and self.approval == self.APPROVAL_APPROVED
            and self.deleted_at is None
        )

    @property
    def ext(self):
        return ".mp4" if self.kind == self.KIND_VIDEO else ".jpg"

    @property
    def local_name(self):
        """File name the agent uses on the vMix PC."""
        stem = slugify(self.original_name.rsplit(".", 1)[0])[:60] or "file"
        return f"{self.pk}-{stem}{self.ext}"

    @property
    def owner_slug(self):
        return slugify(self.owner_name)[:60] or "guest"

    def visible_to(self, guest):
        """May this guest see and pick this file?"""
        if self.deleted_at is not None or self.status == self.STATUS_FAILED:
            return False
        if self.uploaded_by_id == guest.id:
            return self.approval != self.APPROVAL_REJECTED
        if not guest.can_browse:
            return False
        return self.approval == self.APPROVAL_APPROVED and not self.private and self.status == self.STATUS_READY


class MediaSet(models.Model):
    """Ordered list of library files for one guest in one service."""

    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="sets")
    guest = models.ForeignKey(Guest, null=True, blank=True, on_delete=models.CASCADE, related_name="sets")
    title = models.CharField(max_length=200)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]
        constraints = [
            models.UniqueConstraint(fields=["service", "guest"], name="one_set_per_guest_per_service"),
        ]

    def __str__(self):
        return f"{self.title} ({self.service.name})"

    def asset_ids(self):
        return list(self.items.order_by("position", "id").values_list("asset_id", flat=True))


class SetItem(models.Model):
    media_set = models.ForeignKey(MediaSet, on_delete=models.CASCADE, related_name="items")
    asset = models.ForeignKey(MediaAsset, on_delete=models.CASCADE, related_name="set_items")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["media_set", "asset"], name="asset_once_per_set"),
        ]


class Marker(models.Model):
    """A named start point in a video, e.g. 'Baptism · 1:12'."""

    asset = models.ForeignKey(MediaAsset, on_delete=models.CASCADE, related_name="markers")
    label = models.CharField(max_length=60)
    t_ms = models.PositiveIntegerField()
    created_by = models.CharField(max_length=120, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["t_ms"]

    def __str__(self):
        return f"{self.label} @ {self.t_ms} ms"


class Upload(models.Model):
    """A resumable (tus) upload in progress."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guest = models.ForeignKey(Guest, null=True, blank=True, on_delete=models.CASCADE, related_name="tus_uploads")
    filename = models.CharField(max_length=255)
    mime = models.CharField(max_length=120, blank=True)
    length = models.BigIntegerField()
    offset = models.BigIntegerField(default=0)
    completed = models.BooleanField(default=False)
    asset = models.ForeignKey(MediaAsset, null=True, blank=True, on_delete=models.SET_NULL)
    created = models.DateTimeField(auto_now_add=True)

    @property
    def temp_path(self):
        return settings.UPLOAD_TMP_ROOT / f"{self.id}.part"


def hash_token(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Agent(models.Model):
    """A Vitrine agent running on a vMix PC."""

    name = models.CharField(max_length=120)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    token_prefix = models.CharField(max_length=8, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    lan_ip = models.CharField(max_length=64, blank=True)
    listen_port = models.PositiveIntegerField(default=8090)
    version = models.CharField(max_length=40, blank=True)
    status = models.JSONField(default=dict, blank=True, help_text="Last heartbeat from the agent.")
    manifest_version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @classmethod
    def create_with_token(cls, name):
        raw = "vta_" + secrets.token_urlsafe(32)
        agent = cls.objects.create(name=name, token_hash=hash_token(raw), token_prefix=raw[4:12])
        return agent, raw

    def rotate_token(self):
        raw = "vta_" + secrets.token_urlsafe(32)
        self.token_hash = hash_token(raw)
        self.token_prefix = raw[4:12]
        self.save(update_fields=["token_hash", "token_prefix"])
        return raw

    @property
    def is_online(self):
        return self.last_seen is not None and (timezone.now() - self.last_seen).total_seconds() < 90

    @property
    def tablet_base_url(self):
        if not self.lan_ip:
            return ""
        return f"http://{self.lan_ip}:{self.listen_port}"


class ScreenGrant(models.Model):
    """A pairing code issued from the backend for one tablet."""

    ROLE_BACK = "back"
    ROLE_PULPIT = "pulpit"
    ROLE_CHOICES = [(ROLE_BACK, "Back (full control)"), (ROLE_PULPIT, "Pulpit (kiosk)")]

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="grants")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    name = models.CharField(max_length=120)
    nonce = models.CharField(max_length=32, unique=True, default=new_nonce)
    created = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked = models.BooleanField(default=False, help_text="Unpairs the tablet that used this code.")

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.name} ({self.role})"
