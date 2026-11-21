import base64
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase, override_settings
from PIL import Image

from datetime import timedelta

from django.utils import timezone

from .models import Agent, Guest, Location, MediaAsset, Organization, Person, PersonAlias, ScreenGrant, Service
from .signing import private_key

TMP = Path(tempfile.mkdtemp(prefix="vitrine-test-"))


def jpeg_bytes(width=600, height=900):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (40, 120, 200)).save(buffer, "JPEG")
    return buffer.getvalue()


def video_bytes(seconds=3):
    out = TMP / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=720x1280:rate=30:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out),
        ],
        check=True,
    )
    return out.read_bytes()


def make_guest(service, name, org=None):
    """A person (in the event's organization) taking part in the event."""
    org = org or service.location.organization
    person = Person.objects.create(organization=org, name=name)
    return Guest.objects.create(service=service, person=person)


def enter_pin(client, person):
    """Sign this test client's 'device' in to a person's link."""
    person = getattr(person, "person", person)
    response = client.post(f"/g/{person.token}/", {"pin": person.pin})
    assert response.status_code == 302, response.status_code


def b64(text):
    return base64.b64encode(text.encode()).decode()


@override_settings(
    MEDIA_ROOT=TMP / "media",
    UPLOAD_TMP_ROOT=TMP / "media" / "incoming",
    VITRINE_KEY_PATH=TMP / "keys" / "key.pem",
    VITRINE_FILMSTRIP_INTERVAL=1,
)
class FlowTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        private_key.cache_clear()
        self.org = Organization.objects.create(name="Main")
        self.agent, self.agent_token = Agent.create_with_token("vMix PC")
        self.location = Location.objects.create(organization=self.org, name="Zurich hall", agent=self.agent)
        self.service = Service.objects.create(name="Sunday Service", location=self.location)
        self.maria = make_guest(self.service, "Maria")
        self.daniel = make_guest(self.service, "Daniel")
        enter_pin(self.client, self.maria)
        enter_pin(self.client, self.daniel)
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.agent_token}"}

    def tus_upload(self, guest, name, data, chunk=100_000):
        base = f"/g/{guest.token}/tus/"
        response = self.client.post(
            base,
            HTTP_TUS_RESUMABLE="1.0.0",
            HTTP_UPLOAD_LENGTH=str(len(data)),
            HTTP_UPLOAD_METADATA=f"filename {b64(name)},filetype {b64('application/octet-stream')}",
        )
        self.assertEqual(response.status_code, 201)
        location = response["Location"]
        offset = 0
        while offset < len(data):
            piece = data[offset : offset + chunk]
            response = self.client.generic(
                "PATCH",
                location,
                data=piece,
                content_type="application/offset+octet-stream",
                HTTP_UPLOAD_OFFSET=str(offset),
                HTTP_TUS_RESUMABLE="1.0.0",
            )
            self.assertEqual(response.status_code, 204, response.content)
            offset = int(response["Upload-Offset"])
            head = self.client.head(location, HTTP_TUS_RESUMABLE="1.0.0")
            self.assertEqual(int(head["Upload-Offset"]), offset)
        return MediaAsset.all_objects.latest("id")

    def test_photo_upload_approval_library_and_manifest(self):
        asset = self.tus_upload(self.maria, "IMG_1.jpg", jpeg_bytes())
        asset.refresh_from_db()
        self.assertEqual(asset.status, "ready", asset.error)
        self.assertEqual(asset.kind, "photo")
        self.assertEqual((asset.width, asset.height), (1920, 1080))
        self.assertEqual(self.maria.get_set().asset_ids(), [asset.pk])

        # Not approved yet: hidden from Daniel's library and from the agent.
        state = self.client.get(f"/g/{self.daniel.token}/api/state").json()
        self.assertEqual(state["library"], [])
        manifest = self.client.get("/api/agent/manifest", **self.auth).json()
        self.assertEqual(manifest["assets"], [])

        asset.approval = MediaAsset.APPROVAL_APPROVED
        asset.save()
        state = self.client.get(f"/g/{self.daniel.token}/api/state").json()
        self.assertEqual([a["id"] for a in state["library"]], [asset.pk])

        # Daniel borrows Maria's photo.
        state = self.client.post(
            f"/g/{self.daniel.token}/api/set/add", json.dumps({"asset_ids": [asset.pk]}), content_type="application/json"
        ).json()
        self.assertEqual([a["id"] for a in state["set"]], [asset.pk])

        manifest = self.client.get("/api/agent/manifest", **self.auth).json()
        self.assertEqual([a["id"] for a in manifest["assets"]], [asset.pk])
        self.assertEqual(len(manifest["sets"]), 2)

        # Agent downloads with a Range request (resume).
        full = self.client.get(manifest["assets"][0]["file"], **self.auth)
        body = b"".join(full.streaming_content) if hasattr(full, "streaming_content") else full.content
        self.assertEqual(len(body), asset.processed_size)
        part = self.client.get(manifest["assets"][0]["file"], HTTP_RANGE="bytes=100-", **self.auth)
        self.assertEqual(part.status_code, 206)
        self.assertEqual(b"".join(part.streaming_content), body[100:])

        # Private files disappear from other guests' libraries.
        asset.private = True
        asset.save()
        other = make_guest(self.service, "Youth team")
        enter_pin(self.client, other)
        state = self.client.get(f"/g/{other.token}/api/state").json()
        self.assertEqual(state["library"], [])

    def test_video_processing_makes_filmstrip(self):
        asset = self.tus_upload(self.maria, "clip.mov", video_bytes(3))
        asset.refresh_from_db()
        self.assertEqual(asset.status, "ready", asset.error)
        self.assertEqual(asset.kind, "video")
        self.assertGreater(asset.duration_ms, 2500)
        self.assertGreaterEqual(asset.filmstrip_count, 2)
        self.assertTrue(asset.filmstrip)
        info = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate,codec_name",
             "-of", "json", asset.processed.path],
            capture_output=True, text=True, check=True,
        )
        stream = json.loads(info.stdout)["streams"][0]
        self.assertEqual((stream["width"], stream["height"], stream["codec_name"]), (1920, 1080, "h264"))
        self.assertEqual(stream["r_frame_rate"], "25/1")

    def test_agent_auth_and_heartbeat(self):
        self.assertEqual(self.client.get("/api/agent/manifest").status_code, 401)
        response = self.client.post(
            "/api/agent/heartbeat",
            json.dumps({"lan_ip": "192.168.1.50", "listen_port": 8090, "version": "1.0.0", "vmix": {"ok": True}}),
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.lan_ip, "192.168.1.50")
        self.assertTrue(self.agent.is_online)

    def test_pairing_code_is_signed(self):
        response = self.client.post(
            "/api/agent/pairing", json.dumps({"role": "pulpit", "name": "Pulpit"}), content_type="application/json", **self.auth
        )
        token = response.json()["token"]
        prefix, body, signature = token.split(".")
        self.assertEqual(prefix, "s1")
        manifest = self.client.get("/api/agent/manifest", **self.auth).json()
        public = Ed25519PublicKey.from_public_bytes(base64.b64decode(manifest["pubkey"]))
        pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        public.verify(base64.urlsafe_b64decode(pad(signature)), body.encode())
        payload = json.loads(base64.urlsafe_b64decode(pad(body)))
        self.assertEqual(payload["role"], "pulpit")
        self.assertEqual(payload["agent"], self.agent.pk)

        grant = ScreenGrant.objects.get(nonce=payload["nonce"])
        grant.revoked = True
        grant.save()
        manifest = self.client.get("/api/agent/manifest", **self.auth).json()
        self.assertIn(payload["nonce"], manifest["revoked_screens"])

    def test_manage_pages_render(self):
        User.objects.create_superuser("admin", "a@example.org", "pw")
        self.client.login(username="admin", password="pw")
        asset = self.tus_upload(self.maria, "IMG_2.jpg", jpeg_bytes())
        for url in ["/manage/", f"/manage/service/{self.service.pk}/", "/manage/library/", "/manage/library/?trash=1",
                    f"/manage/asset/{asset.pk}/", "/manage/agents/", f"/g/{self.maria.token}/", f"/g/{self.maria.token}/tablet/",
                    "/manage/people/", f"/manage/people/{self.maria.person.pk}/", "/manage/organization/",
                    "/manage/library/?scope=shared", "/manage/settings/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        response = self.client.post("/manage/agents/", {"action": "pair", "agent_id": self.agent.pk, "role": "back", "name": "Back"})
        self.assertContains(response, "/pair?t=s1.")
        # Trash and restore
        self.client.post("/manage/library/", {"action": "trash", "ids": [asset.pk], "confirm": "1"})
        self.assertIsNotNone(MediaAsset.all_objects.get(pk=asset.pk).deleted_at)
        self.assertEqual(self.maria.get_set().asset_ids(), [])
        self.client.post("/manage/library/?trash=1", {"action": "restore", "ids": [asset.pk]})
        self.assertIsNone(MediaAsset.all_objects.get(pk=asset.pk).deleted_at)


@override_settings(VITRINE_PUBLIC_URL="")
class GuestLinkTests(TestCase):
    def test_link_follows_the_manage_address(self):
        User.objects.create_superuser("admin", "a@example.org", "pw")
        self.client.login(username="admin", password="pw")
        org = Organization.objects.create(name="Main")
        service = Service.objects.create(name="Test", location=Location.objects.create(organization=org, name="Hall"))
        guest = make_guest(service, "Maria")
        response = self.client.get(f"/manage/service/{service.pk}/", HTTP_HOST="vitrine.mychurch.ch", secure=True)
        self.assertContains(response, f"https://vitrine.mychurch.ch/g/{guest.token}/")

    @override_settings(VITRINE_PUBLIC_URL="https://guests.mychurch.ch")
    def test_setting_wins(self):
        org = Organization.objects.create(name="Main")
        service = Service.objects.create(name="Test", location=Location.objects.create(organization=org, name="Hall"))
        guest = make_guest(service, "Maria")
        self.assertEqual(guest.person.link, f"https://guests.mychurch.ch/g/{guest.token}/")
        self.assertEqual(guest.link, f"https://guests.mychurch.ch/g/{guest.token}/?e={service.pk}")


@override_settings(VITRINE_VIDEO_FPS=25, VITRINE_FILMSTRIP_INTERVAL=1)
class PassThroughTests(TestCase):
    def make(self, name, size, rate, extra=()):
        out = TMP / name
        TMP.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}:duration=4",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=4:sample_rate=44100",
             "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", *extra, "-c:a", "aac", str(out)],
            check=True,
        )
        return out

    def test_ready_video_is_copied(self):
        from .processing import process_file, probe

        src = self.make("ready.mp4", "1920x1080", 25)
        result = process_file(src, "ready.mp4")
        self.assertTrue(result["passed_through"])
        info = probe(result["processed"])
        audio = [s for s in info["streams"] if s["codec_type"] == "audio"][0]
        self.assertEqual(audio["sample_rate"], "48000")  # audio converted, picture copied
        self.assertIn(result["filmstrip_count"], (4, 5))

    def test_other_frame_rate_is_converted(self):
        from .processing import process_file

        src = self.make("ntsc.mp4", "1920x1080", 30)
        result = process_file(src, "ntsc.mp4")
        self.assertFalse(result["passed_through"])


class AudioTests(TestCase):
    def test_mp3_becomes_audio_with_card(self):
        from .processing import process_file

        TMP.mkdir(parents=True, exist_ok=True)
        src = TMP / "Glorious Day.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
             "-c:a", "libmp3lame", str(src)],
            check=True,
        )
        result = process_file(src, "Glorious Day.mp3", "Maria")
        self.assertEqual(result["kind"], "audio")
        self.assertTrue(str(result["processed"]).endswith(".mp4"))
        self.assertGreater(result["duration_ms"], 2500)
        self.assertTrue(result["thumb"].exists())


class LoginPageTests(TestCase):
    def test_login_pages_are_vitrine_styled(self):
        User.objects.create_superuser("admin", "a@example.org", "pw")
        for url in ["/login/", "/admin/login/"]:
            self.assertContains(self.client.get(url), "login.css")
        self.assertRedirects(self.client.get("/manage/"), "/admin/login/?next=/manage/", fetch_redirect_response=False)
        response = self.client.post("/admin/login/?next=/manage/", {"username": "admin", "password": "nope", "next": "/manage/"})
        self.assertContains(response, 'class="alert"')
        response = self.client.post("/login/", {"username": "admin", "password": "pw", "next": "/manage/"})
        self.assertRedirects(response, "/manage/", fetch_redirect_response=False)
        self.assertContains(self.client.get("/manage/"), "Signed in as admin")
        self.client.post("/logout/")
        self.assertEqual(self.client.get("/manage/").status_code, 302)


class DocsTests(TestCase):
    def test_docs_render_and_search_index(self):
        home = self.client.get("/docs/")
        self.assertContains(home, "Vitrine")
        self.assertContains(home, 'id="search-index"')
        page = self.client.get("/docs/agent/")
        self.assertContains(page, "Bitfocus Companion")
        self.assertContains(page, 'href="/docs/usage/')  # links between docs are rewritten
        self.assertContains(self.client.get("/docs/organizations/"), "Organizations, locations and people")
        self.assertEqual(self.client.get("/docs/nope/").status_code, 404)
        self.assertEqual(self.client.get("/docs/_media/back-tablet.png").status_code, 200)
        self.assertEqual(self.client.get("/docs/_media/../../server/manage.py").status_code, 404)


@override_settings(
    MEDIA_ROOT=TMP / "media",
    UPLOAD_TMP_ROOT=TMP / "media" / "incoming",
    VITRINE_KEY_PATH=TMP / "keys" / "key.pem",
    VITRINE_PRESYNC_DAYS=14,
)
class OrganizationTests(TestCase):
    """Organizations → locations (agents) → events; people roam between them."""

    def setUp(self):
        from .signing import private_key

        private_key.cache_clear()
        today = timezone.localdate()
        self.org = Organization.objects.create(name="Main")
        self.zurich_agent, zt = Agent.create_with_token("Zurich PC")
        self.geneva_agent, gt = Agent.create_with_token("Geneva PC")
        self.zurich = Location.objects.create(organization=self.org, name="Zurich", agent=self.zurich_agent)
        self.geneva = Location.objects.create(organization=self.org, name="Geneva", agent=self.geneva_agent)
        self.zt = {"HTTP_AUTHORIZATION": f"Bearer {zt}"}
        self.gt = {"HTTP_AUTHORIZATION": f"Bearer {gt}"}
        self.z_today = Service.objects.create(name="Zurich today", starts_on=today, ends_on=today, location=self.zurich)
        self.g_next_week = Service.objects.create(name="Geneva", starts_on=today + timedelta(days=7), ends_on=today + timedelta(days=7), location=self.geneva)
        self.g_far = Service.objects.create(name="Geneva later", starts_on=today + timedelta(days=60), ends_on=today + timedelta(days=60), location=self.geneva)
        self.z_old = Service.objects.create(name="Zurich old", starts_on=today - timedelta(days=10), ends_on=today - timedelta(days=10), location=self.zurich)
        self.maria = Person.objects.create(organization=self.org, name="Maria")
        self.photo = MediaAsset.objects.create(
            owner_name="Maria", uploaded_by=self.maria, organization=self.org, original_name="a.jpg", kind="photo",
            status="ready", approval="approved", sha256="x", processed_size=1,
        )

    def manifest(self, auth):
        return self.client.get("/api/agent/manifest", **auth).json()

    def test_each_agent_gets_its_location_window(self):
        self.assertEqual([s["name"] for s in self.manifest(self.zt)["services"]], ["Zurich today"])
        self.assertEqual([s["name"] for s in self.manifest(self.gt)["services"]], ["Geneva"])
        self.assertEqual(self.manifest(self.gt)["location"]["name"], "Geneva")
        # An agent without a location gets nothing.
        loose, raw = Agent.create_with_token("Spare PC")
        self.assertEqual(self.client.get("/api/agent/manifest", HTTP_AUTHORIZATION=f"Bearer {raw}").json()["services"], [])

    def test_person_roams_and_files_presync(self):
        z = Guest.objects.create(service=self.z_today, person=self.maria)
        z.get_set().items.create(asset=self.photo, position=0)
        # Next week in Geneva: same link, set copied from Zurich.
        Guest.objects.create(service=self.g_next_week, person=self.maria)
        enter_pin(self.client, self.maria)
        page = self.client.get(f"/g/{self.maria.token}/")
        self.assertContains(page, "Zurich today")
        self.assertContains(page, "Geneva")  # event switcher
        state = self.client.get(f"/g/{self.maria.token}/api/state?e={self.g_next_week.pk}").json()
        self.assertEqual(state["set"], [])
        self.assertEqual(state["previous_set"]["count"], 1)
        state = self.client.post(
            f"/g/{self.maria.token}/api/set/copy?e={self.g_next_week.pk}",
            json.dumps({"set_id": state["previous_set"]["id"]}), content_type="application/json",
        ).json()
        self.assertEqual([a["id"] for a in state["set"]], [self.photo.pk])
        # Geneva's agent downloads it a week ahead; Zurich's has it today.
        self.assertEqual([a["id"] for a in self.manifest(self.gt)["assets"]], [self.photo.pk])
        self.assertEqual([a["id"] for a in self.manifest(self.zt)["assets"]], [self.photo.pk])

    def test_old_links_and_waiting_page(self):
        PersonAlias.objects.create(person=self.maria, token="old-token-123")
        enter_pin(self.client, self.maria)
        self.assertContains(self.client.get("/g/old-token-123/"), "aren't in an upcoming event")
        self.assertEqual(self.client.get("/g/nope/").status_code, 404)

    def test_sharing_between_organizations(self):
        other = Organization.objects.create(name="Geneva team")
        loc = Location.objects.create(organization=other, name="Lausanne")
        event = Service.objects.create(name="Lausanne", location=loc)
        john = Guest.objects.create(service=event, person=Person.objects.create(organization=other, name="John"))
        enter_pin(self.client, john)
        library = lambda: [a["id"] for a in self.client.get(f"/g/{john.token}/api/state").json()["library"]]  # noqa: E731
        self.assertEqual(library(), [])
        self.org.shares_with.add(other)
        self.assertEqual(library(), [])  # only files marked shared
        self.photo.shared = True
        self.photo.save()
        self.assertEqual(library(), [self.photo.pk])
        state = self.client.get(f"/g/{john.token}/api/state").json()
        self.assertEqual(state["library"][0]["group"], "shared")
        self.photo.shared = False
        self.photo.save()
        self.org.share_all = True
        self.org.save()
        self.assertEqual(library(), [self.photo.pk])

    def test_staff_only_see_their_organization(self):
        other = Organization.objects.create(name="Other")
        stranger = Person.objects.create(organization=other, name="Stranger")
        user = User.objects.create_user("tech", password="pw", is_staff=True)
        self.org.staff.add(user)
        self.client.login(username="tech", password="pw")
        self.assertEqual(self.client.get(f"/manage/people/{self.maria.pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"/manage/people/{stranger.pk}/").status_code, 404)
        self.assertNotContains(self.client.get("/manage/people/"), "Stranger")
        # Adding Maria to the Geneva event from her page.
        self.client.post(f"/manage/people/{self.maria.pk}/", {"action": "add_event", "event_id": self.g_next_week.pk})
        self.assertTrue(Guest.objects.filter(person=self.maria, service=self.g_next_week).exists())
        # Creating an event needs one of our locations.
        response = self.client.post("/manage/", {"name": "Youth night", "starts_on": timezone.localdate().isoformat(), "location": self.zurich.pk, "is_active": "on"})
        self.assertEqual(response.status_code, 302)


@override_settings(MEDIA_ROOT=TMP / "media", UPLOAD_TMP_ROOT=TMP / "media" / "incoming")
class PinTests(TestCase):
    """A link alone isn't enough: each device enters the PIN once."""

    def setUp(self):
        org = Organization.objects.create(name="Main")
        event = Service.objects.create(name="Sunday", location=Location.objects.create(organization=org, name="Hall"))
        self.guest = make_guest(event, "Maria")
        self.person = self.guest.person
        self.base = f"/g/{self.person.token}/"

    def test_pin_needed_then_device_remembered(self):
        self.assertRegex(self.person.pin, r"^\d{6}$")
        page = self.client.get(self.base)
        self.assertContains(page, "Enter the PIN")
        self.assertNotContains(page, "id=\"library\"")
        self.assertEqual(self.client.get(self.base + "api/state").status_code, 403)
        self.assertEqual(self.client.post(self.base + "tus/", HTTP_TUS_RESUMABLE="1.0.0", HTTP_UPLOAD_LENGTH="10").status_code, 403)
        self.assertContains(self.client.post(self.base, {"pin": "000000" if self.person.pin != "000000" else "111111"}), "tries left", status_code=401)
        response = self.client.post(self.base, {"pin": self.person.pin})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.person.devices.count(), 1)
        self.assertEqual(self.client.get(self.base + "api/state").status_code, 200)
        self.assertContains(self.client.get(self.base), "Sign out here")
        # Signed out from the backend: the PIN is needed again.
        self.person.devices.all().delete()
        self.assertEqual(self.client.get(self.base + "api/state").status_code, 403)

    def test_lock_after_wrong_pins(self):
        wrong = "000000" if self.person.pin != "000000" else "111111"
        for _ in range(Person.MAX_PIN_TRIES):
            self.client.post(self.base, {"pin": wrong})
        self.person.refresh_from_db()
        self.assertTrue(self.person.is_locked)
        # Even the right PIN waits until the lock ends.
        self.assertContains(self.client.post(self.base, {"pin": self.person.pin}), "Too many wrong PINs", status_code=401)
        self.assertEqual(self.person.devices.count(), 0)

    def test_staff_skip_the_pin_and_help_page_is_open(self):
        self.assertEqual(self.client.get(self.base + "tablet/").status_code, 200)
        User.objects.create_superuser("admin", "a@example.org", "pw")
        self.client.login(username="admin", password="pw")
        self.assertEqual(self.client.get(self.base + "api/state").status_code, 200)

    def test_new_link_signs_out_devices(self):
        enter_pin(self.client, self.person)
        User.objects.create_superuser("admin", "a@example.org", "pw")
        staff = self.client_class()
        staff.login(username="admin", password="pw")
        staff.post(f"/manage/people/{self.person.pk}/", {"action": "new_pin"})
        old_pin = self.person.pin
        self.person.refresh_from_db()
        self.assertNotEqual(self.person.pin, old_pin)
        self.assertEqual(self.person.devices.count(), 1)  # a new PIN alone keeps devices signed in
        staff.post(f"/manage/people/{self.person.pk}/", {"action": "new_link"})
        self.assertEqual(self.person.devices.count(), 0)

    def test_person_changes_pin_and_staff_reset_lets_them_choose(self):
        enter_pin(self.client, self.person)
        staff_pin = self.person.pin
        # Change my PIN: needs the current one.
        self.assertContains(self.client.post(self.base + "pin", {"current": "999999" if staff_pin != "999999" else "888888", "pin": "4826", "pin2": "4826"}), "isn")
        self.assertContains(self.client.post(self.base + "pin", {"current": staff_pin, "pin": "1111", "pin2": "1111"}), "too easy")
        self.assertContains(self.client.post(self.base + "pin", {"current": staff_pin, "pin": "4826", "pin2": "4826"}), "PIN changed")
        self.person.refresh_from_db()
        self.assertEqual(self.person.pin, "")
        self.assertTrue(self.person.pin_hash.startswith("pbkdf2"))
        other = self.client_class()
        self.assertEqual(other.post(self.base, {"pin": staff_pin}).status_code, 401)
        self.assertEqual(other.post(self.base, {"pin": "4826"}).status_code, 302)
        # Staff reset: PIN cleared, devices out; the next phone chooses.
        User.objects.create_superuser("admin", "a@example.org", "pw")
        staff = self.client_class()
        staff.login(username="admin", password="pw")
        staff.post(f"/manage/people/{self.person.pk}/", {"action": "reset_pin"})
        self.person.refresh_from_db()
        self.assertFalse(self.person.has_pin)
        self.assertEqual(self.person.devices.count(), 0)
        fresh = self.client_class()
        self.assertContains(fresh.get(self.base), "Choose a PIN")
        self.assertContains(fresh.post(self.base, {"pin": "5931", "pin2": "5932"}), "aren", status_code=401)
        self.assertEqual(fresh.post(self.base, {"pin": "5931", "pin2": "5931"}).status_code, 302)
        self.assertEqual(fresh.get(self.base + "api/state").status_code, 200)
        self.assertContains(staff.get(f"/manage/people/{self.person.pk}/"), "chosen by Maria")


@override_settings(VITRINE_TELEGRAM_BOT_TOKEN="123:abc", EMAIL_HOST="smtp.example.org",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AlertTests(TestCase):
    def setUp(self):
        from unittest import mock

        self.org = Organization.objects.create(name="Main", notify_telegram_chat="-100", notify_emails="tech@example.org")
        self.agent, _ = Agent.create_with_token("Zurich PC")
        self.location = Location.objects.create(organization=self.org, name="Zurich hall", agent=self.agent)
        self.sent = []
        patcher = mock.patch("guestmedia.alerts.send_telegram", side_effect=lambda chat, text: self.sent.append(text))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_uploads_waiting_once(self):
        from django.core import mail

        from .tasks import check_pending_uploads

        for name in ("a.jpg", "b.jpg"):
            MediaAsset.objects.create(owner_name="Maria", organization=self.org, original_name=name, kind="photo",
                                      status="ready", approval="pending")
        check_pending_uploads()
        check_pending_uploads()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("2 uploads waiting", self.sent[0])
        self.assertIn("Maria: 2 files", self.sent[0])
        self.assertEqual(len(mail.outbox), 1)

    def test_offline_only_around_an_event(self):
        from .tasks import check_agents

        Agent.objects.filter(pk=self.agent.pk).update(last_seen=timezone.now() - timedelta(minutes=30))
        check_agents()
        self.assertEqual(self.sent, [])  # no event soon: stay quiet
        Service.objects.create(name="Sunday", starts_on=timezone.localdate() + timedelta(days=1), ends_on=timezone.localdate() + timedelta(days=1), location=self.location)
        check_agents()
        check_agents()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("offline", self.sent[0])
        Agent.objects.filter(pk=self.agent.pk).update(last_seen=timezone.now())
        check_agents()
        self.assertIn("back online", self.sent[-1])

    def test_ready_for_tomorrow(self):
        from .tasks import check_sync

        event = Service.objects.create(name="Sunday", starts_on=timezone.localdate() + timedelta(days=1), ends_on=timezone.localdate() + timedelta(days=1), location=self.location)
        guest = make_guest(event, "Maria")
        asset = MediaAsset.objects.create(owner_name="Maria", organization=self.org, original_name="a.jpg", kind="photo",
                                          status="ready", approval="approved", sha256="x")
        guest.get_set().items.create(asset=asset, position=0)
        Agent.objects.filter(pk=self.agent.pk).update(last_seen=timezone.now(), status={"sync": {"missing": [asset.pk]}})
        check_sync()
        self.assertIn("0 of 1 files", self.sent[-1])
        Agent.objects.filter(pk=self.agent.pk).update(status={"sync": {"missing": []}})
        check_sync()
        self.assertIn("all 1 files", self.sent[-1])

    def test_test_message_and_backups_page(self):
        User.objects.create_superuser("admin", "a@example.org", "pw")
        self.client.login(username="admin", password="pw")
        self.client.post("/manage/organization/", {"action": "notifications", "notify_telegram_chat": "-100",
                                                    "notify_uploads": "on", "test": "1"})
        self.assertIn("Test message", self.sent[-1])
        self.assertContains(self.client.get("/manage/settings/"), "Back up to")
        self.assertContains(self.client.get("/manage/"), "Backups are off")


class BackupTests(TransactionTestCase):
    def test_backup_to_a_folder(self):
        import shutil as sh
        import tempfile
        from pathlib import Path

        if not sh.which("rclone"):
            self.skipTest("rclone not installed")
        from .backup import run_backup
        from .models import BackupRun

        dest = Path(tempfile.mkdtemp())
        media = Path(tempfile.mkdtemp())
        (media / "library").mkdir()
        (media / "library" / "a.jpg").write_bytes(b"jpg")
        with override_settings(VITRINE_BACKUP_TARGET=str(dest), MEDIA_ROOT=media, UPLOAD_TMP_ROOT=media / "incoming",
                               VITRINE_BACKUP_DIR=Path(tempfile.mkdtemp())):
            run = BackupRun.objects.get(pk=run_backup("test"))
        self.assertTrue(run.ok, run.log)
        self.assertTrue((dest / "media" / "library" / "a.jpg").exists())
        self.assertEqual(len(list((dest / "db").iterdir())), 1)


class EventPeriodTests(TestCase):
    """Events can last several days, or have no start and/or no end."""

    def setUp(self):
        self.org = Organization.objects.create(name="Main")
        self.agent, raw = Agent.create_with_token("PC")
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
        self.loc = Location.objects.create(organization=self.org, name="Hall", agent=self.agent)
        self.today = timezone.localdate()

    def names(self):
        return [s["name"] for s in self.client.get("/api/agent/manifest", **self.auth).json()["services"]]

    def test_periods_in_the_agent_window(self):
        d = lambda n: self.today + timedelta(days=n)  # noqa: E731
        Service.objects.create(name="Conference", starts_on=d(-1), ends_on=d(1), location=self.loc)
        Service.objects.create(name="Always", starts_on=None, ends_on=None, location=self.loc)
        Service.objects.create(name="Until next week", starts_on=None, ends_on=d(7), location=self.loc)
        Service.objects.create(name="From next month", starts_on=d(40), ends_on=None, location=self.loc)
        Service.objects.create(name="Over", starts_on=d(-10), ends_on=d(-3), location=self.loc)
        Service.objects.create(name="Next week", starts_on=d(5), ends_on=d(5), location=self.loc)
        names = self.names()
        self.assertEqual(set(names), {"Conference", "Always", "Until next week", "Next week"})
        self.assertEqual(names[-1], "Next week")  # events on today come first
        conf = Service.objects.get(name="Conference")
        self.assertIn("–", conf.when)
        self.assertEqual(Service.objects.get(name="Always").when, "ongoing")
        self.assertTrue(Service.objects.get(name="From next month").when.startswith("from"))

    def test_end_date_can_be_removed(self):
        User.objects.create_superuser("admin", "a@example.org", "pw")
        self.client.login(username="admin", password="pw")
        event = Service.objects.create(name="Camp", starts_on=self.today, ends_on=self.today + timedelta(days=2), location=self.loc)
        page = self.client.get(f"/manage/event/{event.pk}/")
        self.assertContains(page, "Empty: no end")
        self.client.post(f"/manage/event/{event.pk}/", {
            "action": "update", "name": "Camp", "starts_on": str(self.today), "ends_on": "",
            "location": self.loc.pk, "is_active": "on",
        })
        event.refresh_from_db()
        self.assertIsNone(event.ends_on)

    def test_person_page_and_form(self):
        User.objects.create_superuser("admin", "a@example.org", "pw")
        self.client.login(username="admin", password="pw")
        r = self.client.post("/manage/", {"name": "Meetings", "starts_on": self.today.isoformat(),
                                          "ends_on": (self.today - timedelta(days=1)).isoformat(), "location": self.loc.pk})
        self.assertContains(r, "The end is before the start")
        r = self.client.post("/manage/", {"name": "Meetings", "starts_on": self.today.isoformat(),
                                          "ends_on": (self.today + timedelta(days=2)).isoformat(), "location": self.loc.pk, "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        event = Service.objects.get(name="Meetings")
        guest = make_guest(event, "Maria")
        self.assertEqual(guest.person.current_participation(), guest)
        self.assertContains(self.client.get(f"/g/{guest.token}/"), event.short_when)


class SettingsPageTests(TestCase):
    """Backups and alert delivery can be set in /manage/settings/ instead of the stack."""

    def setUp(self):
        User.objects.create_superuser("admin", "a@example.org", "pw")
        self.client.login(username="admin", password="pw")

    def test_backup_settings_build_rclone_config(self):
        from . import config

        self.client.post("/manage/settings/", {
            "action": "backup", "backup_kind": "s3", "backup_path": "my-bucket/vitrine",
            "s3__provider": "Other", "s3__endpoint": "https://fsn1.your-objectstorage.com",
            "s3__access_key_id": "AK", "s3__secret_access_key": "SECRET", "backup_time": "02:15",
        })
        plan = config.backup_plan()
        self.assertEqual(plan["target"], "vitrine:my-bucket/vitrine")
        self.assertIn("type = s3", plan["config"])
        self.assertIn("secret_access_key = SECRET", plan["config"])
        self.assertEqual(config.backup_time(), "02:15")
        # The secret isn't shown again, and saving without retyping keeps it.
        page = self.client.get("/manage/settings/")
        self.assertNotContains(page, "SECRET")
        self.client.post("/manage/settings/", {
            "action": "backup", "backup_kind": "s3", "backup_path": "my-bucket/vitrine",
            "s3__provider": "Other", "s3__endpoint": "https://fsn1.your-objectstorage.com",
            "s3__access_key_id": "AK", "s3__secret_access_key": "••••••••",
        })
        self.assertIn("secret_access_key = SECRET", config.backup_plan()["config"])

    def test_google_drive_token_and_off(self):
        from . import config

        self.client.post("/manage/settings/", {
            "action": "backup", "backup_kind": "gdrive", "backup_path": "Vitrine backups",
            "gdrive__token": '{"access_token": "x",\n "expiry": "2026"}', "gdrive__root_folder_id": "",
        })
        plan = config.backup_plan()
        self.assertIn("type = drive", plan["config"])
        self.assertIn('token = {"access_token":"x","expiry":"2026"}', plan["config"])
        self.client.post("/manage/settings/", {"action": "backup", "backup_kind": "off"})
        self.assertIsNone(config.backup_plan())

    def test_alert_delivery_settings(self):
        from unittest import mock
        from . import config

        fake = mock.patch("guestmedia.telegram.api", return_value={"username": "vitrine_test_bot", "first_name": "Vitrine"})
        fake.start()
        self.addCleanup(fake.stop)
        self.client.post("/manage/settings/", {
            "action": "alerts", "telegram_bot_token": "123:abc", "email_host": "smtp.example.org",
            "email_port": "465", "email_security": "ssl", "email_user": "me", "email_password": "pw",
            "offline_alert_minutes": "5", "sync_check_time": "17:30",
        })
        self.assertEqual(config.telegram_token(), "123:abc")
        cfg = config.email_config()
        self.assertTrue(cfg["use_ssl"])
        self.assertEqual(cfg["port"], 465)
        self.assertEqual(config.offline_alert_minutes(), 5)
        self.assertNotContains(self.client.get("/manage/settings/"), "123:abc")


class LiveControlTests(TestCase):
    """A guest's own tablet view: Ask for access, then control the projector through the server."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        org = Organization.objects.create(name="Main")
        self.agent, _ = Agent.create_with_token("Hall PC")
        self.agent.lan_ip = "192.168.1.20"
        self.agent.save()
        self.location = Location.objects.create(organization=org, name="Hall", agent=self.agent)
        self.event = Service.objects.create(name="Sunday", location=self.location)
        self.guest = make_guest(self.event, "Maria")
        self.other = make_guest(self.event, "John")
        from .models import SetItem

        self.mine = MediaAsset.objects.create(owner_name="Maria", original_name="a.jpg", kind="photo", status="ready")
        self.theirs = MediaAsset.objects.create(owner_name="John", original_name="b.jpg", kind="photo", status="ready")
        SetItem.objects.create(media_set=self.guest.get_set(), asset=self.mine)
        SetItem.objects.create(media_set=self.other.get_set(), asset=self.theirs)
        self.base = f"/g/{self.guest.person.token}/"
        enter_pin(self.client, self.guest)

    def report(self, controls=(), requests=(), on_screen=0, event=None):
        from django.core.cache import cache
        from . import relay

        cache.set(relay.live_key(self.agent.pk), {
            "type": "live", "service_id": (event or self.event).pk,
            "controls": list(controls), "requests": list(requests),
            "engine": {"asset_id": on_screen, "kind": "photo"},
        }, 60)

    def test_screen_page_needs_pin_and_renders(self):
        self.assertContains(self.client.get(self.base + "screen/"), 'data-token="')
        other = self.client_class()
        self.assertNotContains(other.get(self.base + "screen/"), "guest_screen.js", status_code=403)

    def test_offline_and_not_here(self):
        live = self.client.get(self.base + "api/live").json()
        self.assertFalse(live["online"])
        response = self.client.post(self.base + "api/live/request_access", "{}", content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.report(event=Service.objects.create(name="Other", location=self.location))
        live = self.client.get(self.base + "api/live").json()
        self.assertTrue(live["online"])
        self.assertFalse(live["here"])

    def test_other_people_files_stay_private(self):
        self.report(on_screen=self.theirs.pk)
        live = self.client.get(self.base + "api/live").json()
        self.assertIsNone(live["live"])
        self.assertTrue(live["other_on_screen"])
        self.report(on_screen=self.mine.pk)
        self.assertEqual(self.client.get(self.base + "api/live").json()["live"]["asset_id"], self.mine.pk)

    def test_needs_control_before_taking(self):
        from unittest import mock

        set_id = self.guest.get_set().pk
        self.report()
        with mock.patch("guestmedia.relay.send_command", return_value={"ok": True}) as send:
            take = self.client.post(self.base + "api/live/take", json.dumps({"asset_id": self.mine.pk}), content_type="application/json")
            self.assertEqual(take.status_code, 403)
            ask = self.client.post(self.base + "api/live/request_access", "{}", content_type="application/json")
            self.assertEqual(ask.status_code, 200)
            self.assertEqual(send.call_args[0][1]["action"], "request_access")
            self.assertEqual(send.call_args[0][1]["set_id"], set_id)
            self.report(requests=[set_id])
            self.assertTrue(self.client.get(self.base + "api/live").json()["requested"])
            # The desk gives control.
            self.report(controls=[set_id])
            self.assertTrue(self.client.get(self.base + "api/live").json()["control"])
            not_mine = self.client.post(self.base + "api/live/take", json.dumps({"asset_id": self.theirs.pk}), content_type="application/json")
            self.assertEqual(not_mine.status_code, 403)
            take = self.client.post(self.base + "api/live/take", json.dumps({"asset_id": self.mine.pk}), content_type="application/json")
            self.assertEqual(take.status_code, 200)
            self.assertEqual(send.call_args[0][1]["asset_id"], self.mine.pk)
            self.assertEqual(send.call_args[0][1]["by"], "Maria")
        self.assertEqual(self.client.post(self.base + "api/live/reboot").status_code, 404)

    def test_relay_round_trip(self):
        from unittest import mock
        from django.core.cache import cache
        from . import relay

        def fake_send(group, message):
            self.assertEqual(group, relay.agent_group(self.agent.pk))
            cache.set(relay.result_key(message["command"]["id"]), {"ok": True, "error": ""}, 60)

        layer = mock.Mock()
        layer.group_send = mock.AsyncMock(side_effect=fake_send)
        with mock.patch("guestmedia.relay.get_channel_layer", return_value=layer):
            self.assertTrue(relay.send_command(self.agent, {"action": "clear"})["ok"])
        layer.group_send = mock.AsyncMock()
        with mock.patch("guestmedia.relay.get_channel_layer", return_value=layer):
            self.assertFalse(relay.send_command(self.agent, {"action": "clear"}, wait=0.3)["ok"])

    def test_permanent_tablet_link(self):
        response = self.client.get(f"/t/{self.location.tablet_slug}/")
        self.assertRedirects(response, "http://192.168.1.20:8090/screen", fetch_redirect_response=False)
        self.agent.lan_ip = ""
        self.agent.save()
        self.assertEqual(self.client.get(f"/t/{self.location.tablet_slug}/").status_code, 503)
        self.assertEqual(self.client.get("/t/nope/").status_code, 404)
        self.assertNotEqual(self.location.tablet_slug, Location.objects.create(organization=self.location.organization, name="B").tablet_slug)


class TelegramLinkTests(TestCase):
    """Organization → Connect Telegram: no chat ids to look up."""

    def setUp(self):
        from unittest import mock
        from django.core.cache import cache

        cache.clear()
        User.objects.create_superuser("admin", "a@example.org", "pw")
        self.client.login(username="admin", password="pw")
        self.org = Organization.objects.create(name="Main")
        from .models import SiteSettings

        site = SiteSettings.load()
        site.telegram_bot_token = "123:abc"
        site.save()
        self.updates = []
        self.sent = []

        def fake_api(method, token=None, **params):
            if method == "getMe":
                return {"username": "vitrine_test_bot", "first_name": "Vitrine"}
            if method == "getUpdates":
                if "offset" in params:
                    self.updates = [u for u in self.updates if u["update_id"] >= params["offset"]]
                    return []
                return list(self.updates)
            if method == "sendMessage":
                self.sent.append((params["chat_id"], params["text"]))
                return {}
            raise AssertionError(method)

        patcher = mock.patch("guestmedia.telegram.api", side_effect=fake_api)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_connect_a_group(self):
        page = self.client.get("/manage/organization/")
        self.assertContains(page, "Connect Telegram")
        self.client.post("/manage/organization/", {"action": "telegram_connect"})
        page = self.client.get("/manage/organization/")
        code = self.client.session["vitrine_tg_code"]
        self.assertContains(page, f"https://t.me/vitrine_test_bot?startgroup={code}")
        self.assertEqual(self.client.get("/manage/organization/telegram-check").json()["status"], "waiting")
        # Someone else's /start and a wrong code are ignored; the group's /start@bot <code> links it.
        self.updates = [
            {"update_id": 5, "message": {"text": "/start", "chat": {"id": 1, "type": "private", "first_name": "X"}}},
            {"update_id": 6, "message": {"text": "/start@vitrine_test_bot wrongcode1", "chat": {"id": 2, "type": "group", "title": "Other"}}},
            {"update_id": 7, "message": {"text": f"/start@vitrine_test_bot {code}", "chat": {"id": -100555, "type": "supergroup", "title": "AV team"}}},
        ]
        result = self.client.get("/manage/organization/telegram-check").json()
        self.assertEqual(result, {"status": "linked", "title": "AV team"})
        self.org.refresh_from_db()
        self.assertEqual(self.org.notify_telegram_chat, "-100555")
        self.assertEqual(self.org.notify_telegram_title, "AV team")
        self.assertIn("Main", self.sent[-1][1])
        self.assertEqual(self.updates, [])  # marked as read
        self.assertContains(self.client.get("/manage/organization/"), "Alerts go to <strong>AV team</strong>")
        self.client.post("/manage/organization/", {"action": "telegram_disconnect"})
        self.org.refresh_from_db()
        self.assertEqual(self.org.notify_telegram_chat, "")

    def test_group_moved_to_supergroup(self):
        from . import alerts, telegram

        self.org.notify_telegram_chat = "-42"
        self.org.save()
        calls = []

        def moved(method, token=None, **params):
            calls.append(params.get("chat_id"))
            if params.get("chat_id") == "-42":
                raise telegram.TelegramError("group chat was upgraded to a supergroup chat", {"migrate_to_chat_id": -100999})
            return {}

        from unittest import mock

        with mock.patch("guestmedia.telegram.api", side_effect=moved):
            self.assertEqual(alerts.notify(self.org, "Test", "x"), [])
        self.org.refresh_from_db()
        self.assertEqual(self.org.notify_telegram_chat, "-100999")

    def test_settings_show_bot_and_presets(self):
        page = self.client.get("/manage/settings/")
        self.assertContains(page, "@vitrine_test_bot")
        self.assertContains(page, "smtp-relay.brevo.com")


class EditDeleteTests(TestCase):
    """Duplicate/delete events, accounts and the team, delete an organization."""

    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "a@example.org", "pw-long-enough")
        self.client.login(username="admin", password="pw-long-enough")
        self.org = Organization.objects.create(name="Main")
        self.org.staff.add(self.admin)
        self.loc = Location.objects.create(organization=self.org, name="Hall")
        self.event = Service.objects.create(name="Camp", location=self.loc, auto_approve=True)
        self.guest = make_guest(self.event, "Maria")
        from .models import SetItem

        self.asset = MediaAsset.objects.create(owner_name="Maria", original_name="a.jpg", kind="photo", status="ready", organization=self.org)
        SetItem.objects.create(media_set=self.guest.get_set(), asset=self.asset)

    def test_duplicate_and_delete_event(self):
        url = f"/manage/event/{self.event.pk}/"
        self.assertContains(self.client.get(url), "Duplicate event")
        response = self.client.post(url, {"action": "duplicate", "name": "Camp 2027", "starts_on": "2027-07-01",
                                          "ends_on": "2027-07-03", "with_people": "on", "with_files": "on"})
        copy = Service.objects.get(name="Camp 2027")
        self.assertRedirects(response, f"/manage/event/{copy.pk}/", fetch_redirect_response=False)
        self.assertEqual((copy.location, copy.auto_approve, str(copy.ends_on)), (self.loc, True, "2027-07-03"))
        new_guest = copy.guests.get()
        self.assertEqual(new_guest.person, self.guest.person)
        self.assertEqual(new_guest.get_set().asset_ids(), [self.asset.pk])
        # Delete needs the name typed.
        self.client.post(url, {"action": "delete_event", "confirm_name": "nope"})
        self.assertTrue(Service.objects.filter(pk=self.event.pk).exists())
        self.client.post(url, {"action": "delete_event", "confirm_name": "Camp"})
        self.assertFalse(Service.objects.filter(pk=self.event.pk).exists())
        self.assertTrue(Person.objects.filter(pk=self.guest.person.pk).exists())
        self.assertTrue(MediaAsset.objects.filter(pk=self.asset.pk).exists())

    def test_my_account(self):
        self.client.post("/manage/account/", {"action": "profile", "first_name": "Silvan", "email": "s@example.org"})
        self.admin.refresh_from_db()
        self.assertEqual((self.admin.first_name, self.admin.email), ("Silvan", "s@example.org"))
        self.client.post("/manage/account/", {"action": "password", "old_password": "wrong", "new_password1": "brand-new-pw", "new_password2": "brand-new-pw"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("pw-long-enough"))
        self.client.post("/manage/account/", {"action": "password", "old_password": "pw-long-enough", "new_password1": "brand-new-pw", "new_password2": "brand-new-pw"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("brand-new-pw"))
        self.assertEqual(self.client.get("/manage/account/").status_code, 200)  # still signed in

    def test_team(self):
        response = self.client.post("/manage/team/", {"username": "tech", "email": "t@example.org", "orgs": [self.org.pk]})
        tech = User.objects.get(username="tech")
        self.assertTrue(tech.is_staff and not tech.is_superuser)
        self.assertIn(self.org, tech.vitrine_orgs.all())
        page = self.client.get(response["Location"])
        password = page.context["new_password"]["password"]
        self.assertTrue(tech.check_password(password))
        self.assertIsNone(self.client.get(response["Location"]).context["new_password"])  # shown once
        self.client.post(f"/manage/team/{tech.pk}/", {"action": "update", "email": "x@example.org", "orgs": []})
        tech.refresh_from_db()
        self.assertFalse(tech.is_active)  # "Can sign in" unticked
        self.assertEqual(tech.vitrine_orgs.count(), 0)
        # Can't lock yourself out.
        self.client.post(f"/manage/team/{self.admin.pk}/", {"action": "update"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_superuser and self.admin.is_active)
        self.client.post(f"/manage/team/{self.admin.pk}/", {"action": "delete"})
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())
        self.client.post(f"/manage/team/{tech.pk}/", {"action": "delete"})
        self.assertFalse(User.objects.filter(username="tech").exists())
        # Staff who aren't superusers can't see the team.
        staff = User.objects.create_user("staff", password="pw-long-enough", is_staff=True)
        self.org.staff.add(staff)
        self.client.login(username="staff", password="pw-long-enough")
        self.assertEqual(self.client.get("/manage/team/").status_code, 404)

    def test_delete_organization_only_when_empty(self):
        other = Organization.objects.create(name="Other")
        session = self.client.session
        session["vitrine_org"] = other.pk
        session.save()
        self.client.post("/manage/organization/", {"action": "delete_org", "confirm_name": "Other"})
        self.assertFalse(Organization.objects.filter(pk=other.pk).exists())
        session = self.client.session
        session["vitrine_org"] = self.org.pk
        session.save()
        self.client.post("/manage/organization/", {"action": "delete_org", "confirm_name": "Main"})
        self.assertTrue(Organization.objects.filter(pk=self.org.pk).exists())  # has a location, people, files
