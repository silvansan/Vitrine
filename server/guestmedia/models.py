"""Data model.

Organization → Locations (one agent = one vMix PC each) → Events.
People belong to an organization and roam: one permanent link, used at any
event of any location. Being in an event (Guest) gives the person a set there.

Files live once in the library (MediaAsset). A set (MediaSet) is an ordered
list that points at library files, so the same file can be in several sets
without being copied. An agent only downloads the files in the sets of its
own location's upcoming events.

Events are still called "Service" in code and in the agent protocol.
"""
import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


def new_tablet_slug():
    return secrets.token_urlsafe(9)


def new_pin():
    """A 6-digit PIN for a person's link."""
    return f"{secrets.randbelow(10**6):06d}"


def new_guest_token():
    return secrets.token_urlsafe(18)


def new_nonce():
    return secrets.token_hex(12)


class Organization(models.Model):
    """A team with its own staff, people, locations and library."""

    name = models.CharField(max_length=120, unique=True)
    staff = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="vitrine_orgs",
        help_text="Staff accounts that manage this organization. Superusers see every organization.",
    )
    shares_with = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="shared_from",
        help_text="Organizations that may use our shared files.",
    )
    share_all = models.BooleanField(
        default=False,
        help_text="Share every approved, non-private file. Otherwise only files marked 'shared'.",
    )
    # Where staff hear about things that need them (see guestmedia/alerts.py).
    notify_telegram_chat = models.CharField(
        max_length=64, blank=True, help_text="Telegram chat or group id (linked from /manage/organization/)."
    )
    notify_telegram_title = models.CharField(max_length=120, blank=True, help_text="Name of the linked chat.")
    notify_emails = models.TextField(blank=True, help_text="Email addresses, one per line or separated by commas.")
    notify_uploads = models.BooleanField(default=True, help_text="Uploads waiting for approval.")
    notify_offline = models.BooleanField(default=True, help_text="A vMix PC goes offline before or during an event.")
    notify_sync = models.BooleanField(default=True, help_text="The day before an event: are all its files on the vMix PC?")
    notify_backup = models.BooleanField(default=True, help_text="A backup failed.")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def shares_asset(self, asset):
        return self.share_all or asset.shared


class Location(models.Model):
    """A venue or hall. One location is one agent, which is one vMix PC."""

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="locations")
    name = models.CharField(max_length=120)
    agent = models.OneToOneField(
        "Agent", null=True, blank=True, on_delete=models.SET_NULL, related_name="location",
        help_text="The vMix PC at this location.",
    )
    # /t/<slug>/ always leads paired tablets to this location's vMix PC, whatever its IP.
    tablet_slug = models.CharField(max_length=24, unique=True, default=new_tablet_slug)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["organization__name", "name"]

    def __str__(self):
        return f"{self.name} ({self.organization.name})"


class Person(models.Model):
    """Someone who brings media. Roams between events with one permanent link."""

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="people")
    name = models.CharField(max_length=120)
    token = models.CharField(max_length=64, unique=True, default=new_guest_token)
    can_upload = models.BooleanField(default=True)
    can_browse = models.BooleanField(default=True, help_text="May pick files from the shared library.")
    expires_at = models.DateTimeField(null=True, blank=True)
    # Asked once per phone or computer; that device is then remembered until signed out.
    # Normally set by staff (kept readable so staff can tell it to the person). The
    # person can change it on their page; a PIN they chose is stored hashed, like a
    # password. Staff can also clear it: then the next device that opens the link
    # chooses one.
    pin = models.CharField(max_length=12, blank=True, default=new_pin, help_text="A PIN set by staff (readable).")
    pin_hash = models.CharField(max_length=128, blank=True, default="", help_text="A PIN the person chose (hashed).")
    pin_set_at = models.DateTimeField(null=True, blank=True)
    pin_set_from = models.CharField(max_length=160, blank=True, default="", help_text="Device that chose the PIN.")
    failed_pins = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    MAX_PIN_TRIES = 5
    LOCK_MINUTES = 15

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "people"

    @property
    def has_pin(self):
        return bool(self.pin or self.pin_hash)

    @property
    def pin_chosen_by_person(self):
        return bool(self.pin_hash)

    def check_pin_value(self, value):
        import hmac

        from django.contrib.auth.hashers import check_password

        if self.pin_hash:
            return check_password(value, self.pin_hash)
        return bool(self.pin) and hmac.compare_digest(value, self.pin)

    def choose_pin(self, value, device_label=""):
        """The person picks their own PIN (stored hashed)."""
        from django.contrib.auth.hashers import make_password

        self.pin_hash = make_password(value)
        self.pin = ""
        self.pin_set_at = timezone.now()
        self.pin_set_from = device_label[:160]
        self.failed_pins = 0
        self.locked_until = None

    def set_staff_pin(self):
        self.pin = new_pin()
        self.pin_hash = ""
        self.pin_set_at = timezone.now()
        self.pin_set_from = ""
        self.failed_pins = 0
        self.locked_until = None

    def reset_pin(self):
        """No PIN: the next device that opens the link chooses one."""
        self.pin = ""
        self.pin_hash = ""
        self.pin_set_at = None
        self.pin_set_from = ""
        self.failed_pins = 0
        self.locked_until = None

    @property
    def is_locked(self):
        return self.locked_until is not None and self.locked_until > timezone.now()

    def __str__(self):
        return self.name

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at < timezone.now()

    def link_for(self, request=None, event=None):
        """The person's link. Uses VITRINE_PUBLIC_URL, or else the address of the current request."""
        base = settings.VITRINE_PUBLIC_URL
        if not base and request is not None:
            base = f"{request.scheme}://{request.get_host()}"
        link = f"{base}/g/{self.token}/"
        return f"{link}?e={event.pk}" if event else link

    @property
    def link(self):
        return self.link_for()

    def participations(self):
        return self.guests.select_related("service", "service__location").order_by("service__starts_on", "service_id")

    def upcoming_participations(self):
        """Events that aren't over (from yesterday on), today's first."""
        yesterday = timezone.localdate() - timedelta(days=1)
        today = timezone.localdate()
        guests = [g for g in self.participations() if not g.service.is_past(yesterday)]
        return sorted(guests, key=lambda g: g.service.sort_key(today))

    def current_participation(self):
        """The event to show by default: one that is on today, else the next one, else the latest."""
        active = [g for g in self.upcoming_participations() if g.service.is_active]
        if active:
            return active[0]
        return self.participations().order_by("-service__starts_on", "-service_id").first()


class PersonDevice(models.Model):
    """A phone or computer that entered the person's PIN. Remembered until signed out."""

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="devices")
    token_hash = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=160, blank=True, help_text="Browser and system, from the user agent.")
    ip = models.GenericIPAddressField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_seen"]

    def __str__(self):
        return f"{self.person.name}: {self.label or 'device'}"


class PersonAlias(models.Model):
    """An older link that still opens a person's page (e.g. from before people were merged)."""

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="aliases")
    token = models.CharField(max_length=64, unique=True)

    def __str__(self):
        return f"{self.person.name} ({self.token[:6]}…)"


class ServiceQuerySet(models.QuerySet):
    def not_over(self, day):
        """Events that haven't ended before this day."""
        return self.filter(models.Q(ends_on__isnull=True) | models.Q(ends_on__gte=day))

    def overlapping(self, first, last):
        """Events that are on at some point between first and last (inclusive)."""
        return self.filter(
            (models.Q(starts_on__isnull=True) | models.Q(starts_on__lte=last))
            & (models.Q(ends_on__isnull=True) | models.Q(ends_on__gte=first))
        )


class Service(models.Model):
    """An event: a date at a location (called "service" in code and in the agent protocol)."""

    location = models.ForeignKey(
        Location, null=True, blank=True, on_delete=models.SET_NULL, related_name="events",
        help_text="The agent of this location shows the event and downloads its files ahead of time.",
    )
    name = models.CharField(max_length=200)
    # A single day, several days (e.g. a 3-day conference), open-ended (only a start or
    # only an end) or ongoing (neither).
    starts_on = models.DateField(null=True, blank=True, default=timezone.localdate, help_text="Empty: already running.")
    ends_on = models.DateField(null=True, blank=True, help_text="Empty: no end. Same as the start for a one-day event.")
    is_active = models.BooleanField(
        default=True,
        help_text="Active events that are on, or start within the next days, are sent to their location's agent.",
    )

    objects = ServiceQuerySet.as_manager()
    auto_approve = models.BooleanField(
        default=False,
        help_text="Skip the approval queue for uploads from this event's people.",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_on", "-id"]
        verbose_name = "event"

    def __str__(self):
        where = f" · {self.location.name}" if self.location_id else ""
        return f"{self.name} · {self.when}{where}"

    def is_on(self, day):
        return (self.starts_on is None or self.starts_on <= day) and (self.ends_on is None or self.ends_on >= day)

    def is_past(self, day):
        return self.ends_on is not None and self.ends_on < day

    @property
    def when(self):
        """'Sun 29 Nov 2026', '27–29 Nov 2026', 'from 27 Nov 2026', 'until 29 Nov 2026' or 'ongoing'."""
        start, end = self.starts_on, self.ends_on
        if start and end:
            if start == end:
                return f"{start:%a} {start.day} {start:%b %Y}"
            if (start.year, start.month) == (end.year, end.month):
                return f"{start.day}–{end.day} {end:%b %Y}"
            if start.year == end.year:
                return f"{start.day} {start:%b} – {end.day} {end:%b %Y}"
            return f"{start.day} {start:%b %Y} – {end.day} {end:%b %Y}"
        if start:
            return f"from {start.day} {start:%b %Y}"
        if end:
            return f"until {end.day} {end:%b %Y}"
        return "ongoing"

    @property
    def short_when(self):
        """Like when, without years: for menus and chips."""
        start, end = self.starts_on, self.ends_on
        if start and end:
            if start == end:
                return f"{start:%a} {start.day} {start:%b}"
            if (start.year, start.month) == (end.year, end.month):
                return f"{start.day}–{end.day} {end:%b}"
            return f"{start.day} {start:%b} – {end.day} {end:%b}"
        if start:
            return f"from {start.day} {start:%b}"
        if end:
            return f"until {end.day} {end:%b}"
        return "ongoing"

    def sort_key(self, today):
        """Happening today first, then by start (no start counts as already started)."""
        from datetime import date

        return (not self.is_on(today), self.starts_on or date.min, self.id)

    @property
    def organization(self):
        return self.location.organization if self.location_id else None


class Guest(models.Model):
    """A person taking part in one event. Gives them a set there."""

    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="guests")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="guests")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["person__name"]
        constraints = [
            models.UniqueConstraint(fields=["service", "person"], name="person_once_per_event"),
        ]

    def __str__(self):
        return f"{self.person.name} ({self.service.name})"

    # The link, rights and expiry belong to the person.
    @property
    def name(self):
        return self.person.name

    @property
    def token(self):
        return self.person.token

    @property
    def can_upload(self):
        return self.person.can_upload

    @property
    def can_browse(self):
        return self.person.can_browse

    @property
    def is_expired(self):
        return self.person.is_expired

    def link_for(self, request=None):
        return self.person.link_for(request, self.service)

    @property
    def link(self):
        return self.link_for()

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
    KIND_AUDIO = "audio"
    KIND_CHOICES = [(KIND_PHOTO, "Photo"), (KIND_VIDEO, "Video"), (KIND_AUDIO, "Audio")]
    TIMED_KINDS = (KIND_VIDEO, KIND_AUDIO)

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
    organization = models.ForeignKey(
        Organization, null=True, blank=True, on_delete=models.SET_NULL, related_name="assets",
        help_text="Whose library the file is in.",
    )
    uploaded_by = models.ForeignKey(Person, null=True, blank=True, on_delete=models.SET_NULL, related_name="uploads")
    service = models.ForeignKey(Service, null=True, blank=True, on_delete=models.SET_NULL, related_name="uploads")
    original_name = models.CharField(max_length=255)
    original = models.FileField(upload_to=original_upload_path, max_length=500, blank=True)
    original_size = models.BigIntegerField(default=0)

    kind = models.CharField(max_length=10, choices=KIND_CHOICES, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_PROCESSING)
    error = models.TextField(blank=True)
    approval = models.CharField(max_length=10, choices=APPROVAL_CHOICES, default=APPROVAL_PENDING)
    private = models.BooleanField(default=False, help_text="Only the uploader can pick it; hidden from the shared library.")
    shared = models.BooleanField(
        default=False, help_text="Other organizations we share with may use it (always, if the organization shares everything)."
    )
    pending_notified = models.BooleanField(default=False, help_text="Staff were told it waits for approval.")

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
        return ".mp4" if self.is_timed else ".jpg"

    @property
    def is_timed(self):
        """Videos and audio play over time (progress, cue, markers)."""
        return self.kind in self.TIMED_KINDS

    @property
    def local_name(self):
        """File name the agent uses on the vMix PC."""
        stem = slugify(self.original_name.rsplit(".", 1)[0])[:60] or "file"
        return f"{self.pk}-{stem}{self.ext}"

    @property
    def owner_slug(self):
        return slugify(self.owner_name)[:60] or "guest"

    def visible_to(self, person):
        """May this person see and pick this file? (A Guest works too: its person is used.)"""
        person = getattr(person, "person", person)
        if self.deleted_at is not None or self.status == self.STATUS_FAILED:
            return False
        if self.uploaded_by_id == person.id:
            return self.approval != self.APPROVAL_REJECTED
        if not person.can_browse:
            return False
        if not (self.approval == self.APPROVAL_APPROVED and not self.private and self.status == self.STATUS_READY):
            return False
        return self.organization_id is None or self.organization_id == person.organization_id or self.is_shared_with(person.organization)

    def is_shared_with(self, organization):
        owner = self.organization
        if owner is None or organization is None or owner.pk == organization.pk:
            return False
        return owner.shares_asset(self) and owner.shares_with.filter(pk=organization.pk).exists()

    @classmethod
    def library_for(cls, organization):
        """Approved, ready, non-private files an organization's people may pick: its own and those shared with it."""
        from django.db.models import Q

        base = cls.objects.filter(status=cls.STATUS_READY, approval=cls.APPROVAL_APPROVED, private=False)
        if organization is None:
            return base.filter(organization__isnull=True)
        sharing = Organization.objects.filter(shares_with=organization)
        return base.filter(
            Q(organization=organization)
            | Q(organization__isnull=True)
            | Q(organization__in=sharing.filter(share_all=True))
            | Q(organization__in=sharing, shared=True)
        )


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
    # Uploads by staff from /manage/ (no guest): who, for which organization, and optionally
    # for which person (their library) and event (their set in it).
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    organization = models.ForeignKey(Organization, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    person = models.ForeignKey(Person, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    service = models.ForeignKey(Service, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
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
    offline_notified = models.BooleanField(default=False, help_text="Staff were told it went offline.")

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
    def offline_since(self):
        return self.last_seen if self.last_seen and not self.is_online else None

    @property
    def current_manifest_version(self):
        """Changes with every content change and once a day, so the agent picks up the moving event window."""
        return self.manifest_version * 1000 + timezone.localdate().toordinal() % 1000

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


class BackupRun(models.Model):
    """One backup: database dump plus media, copied to VITRINE_BACKUP_TARGET with rclone."""

    started = models.DateTimeField(auto_now_add=True)
    finished = models.DateTimeField(null=True, blank=True)
    ok = models.BooleanField(default=False)
    target = models.CharField(max_length=300)
    database_file = models.CharField(max_length=200, blank=True)
    database_bytes = models.BigIntegerField(default=0)
    log = models.TextField(blank=True)

    class Meta:
        ordering = ["-started"]

    def __str__(self):
        return f"Backup {self.started:%Y-%m-%d %H:%M} {'ok' if self.ok else 'failed'}"

    @property
    def duration(self):
        return (self.finished - self.started) if self.finished else None


class SiteSettings(models.Model):
    """Server-wide settings edited in /manage/settings/ (one row). Empty fields fall back to the stack environment."""

    BACKUP_KINDS = [
        ("off", "Off"),
        ("folder", "Folder on the server (USB disk, NAS mounted on the host)"),
        ("s3", "S3-compatible (Hetzner, AWS, MinIO, Wasabi, Backblaze…)"),
        ("azure", "Azure Blob Storage"),
        ("gdrive", "Google Drive"),
        ("opendrive", "OpenDrive"),
        ("ftp", "FTP / FTPS"),
        ("sftp", "SFTP"),
        ("smb", "SMB / Windows share / NAS"),
        ("custom", "Other rclone remote (paste its configuration)"),
    ]

    telegram_bot_token = models.CharField(max_length=200, blank=True)
    email_host = models.CharField(max_length=200, blank=True)
    email_port = models.PositiveIntegerField(null=True, blank=True)
    email_user = models.CharField(max_length=200, blank=True)
    email_password = models.CharField(max_length=200, blank=True)
    email_security = models.CharField(
        max_length=10, blank=True, choices=[("starttls", "STARTTLS"), ("ssl", "SSL/TLS"), ("none", "None")]
    )
    email_from = models.CharField(max_length=200, blank=True)
    offline_alert_minutes = models.PositiveIntegerField(null=True, blank=True)
    sync_check_time = models.CharField(max_length=5, blank=True, help_text="HH:MM")

    backup_kind = models.CharField(max_length=12, blank=True, choices=BACKUP_KINDS)
    backup_path = models.CharField(
        max_length=300, blank=True, help_text="Folder, bucket/folder or share/folder on the target."
    )
    backup_options = models.JSONField(default=dict, blank=True, help_text="Connection details of the chosen kind.")
    backup_time = models.CharField(max_length=5, blank=True, help_text="HH:MM")
    backup_keep_days = models.PositiveIntegerField(null=True, blank=True)
    last_backup_day = models.DateField(null=True, blank=True)
    last_sync_check_day = models.DateField(null=True, blank=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "settings"
        verbose_name_plural = "settings"

    def __str__(self):
        return "Vitrine settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
