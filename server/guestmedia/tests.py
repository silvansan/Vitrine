import base64
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from PIL import Image

from .models import Agent, Guest, MediaAsset, ScreenGrant, Service
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
        self.service = Service.objects.create(name="Sunday Service")
        self.maria = Guest.objects.create(service=self.service, name="Maria")
        self.daniel = Guest.objects.create(service=self.service, name="Daniel")
        self.agent, self.agent_token = Agent.create_with_token("vMix PC")
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
        other = Guest.objects.create(service=self.service, name="Youth team")
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
                    f"/manage/asset/{asset.pk}/", "/manage/agents/", f"/g/{self.maria.token}/"]:
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
        service = Service.objects.create(name="Test")
        guest = Guest.objects.create(service=service, name="Maria")
        response = self.client.get(f"/manage/service/{service.pk}/", HTTP_HOST="vitrine.mychurch.ch", secure=True)
        self.assertContains(response, f"https://vitrine.mychurch.ch/g/{guest.token}/")

    @override_settings(VITRINE_PUBLIC_URL="https://guests.mychurch.ch")
    def test_setting_wins(self):
        service = Service.objects.create(name="Test")
        guest = Guest.objects.create(service=service, name="Maria")
        self.assertEqual(guest.link, f"https://guests.mychurch.ch/g/{guest.token}/")


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
