"""Turn whatever a phone uploads into files vMix plays reliably.

Photos: HEIC/JPG/PNG -> 1920x1080 JPG, EXIF rotation applied, portrait shots
on a blurred copy of themselves instead of black bars.
Videos: anything ffmpeg reads -> H.264 High 1080p, constant frame rate, AAC
48 kHz stereo, faststart; same blurred fill for vertical video. Videos that are
already H.264 1920x1080 at the project frame rate are copied without
re-encoding (only the audio is converted if needed), which takes seconds.
Audio (MP3, M4A, WAV…): an MP4 with the sound (AAC 48 kHz) and a still card
(cover art or title) as picture; the card is also the thumbnail. The agent can
keep the default input on the projector while it plays, or show the card.
Photos and videos get a thumbnail; videos also get a filmstrip for the cue panel.
"""
import hashlib
import json
import logging
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

logger = logging.getLogger(__name__)

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover - optional dependency
    logger.warning("pillow-heif not available; HEIC photos will fail")

TARGET_W = 1920
TARGET_H = 1080
THUMB_W = 480
THUMB_H = 270
FRAME_W = 192
FRAME_H = 108
FILMSTRIP_COLS = 10
MAX_FRAMES = 120

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus", ".wma", ".aif", ".aiff"}


class ProcessingError(Exception):
    pass


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(cmd):
    logger.info("run: %s", " ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ProcessingError(result.stderr[-2000:])
    return result.stdout


def probe(path):
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    return json.loads(out)


def detect_kind(path, original_name):
    ext = Path(original_name).suffix.lower()
    if ext in PHOTO_EXTS:
        return "photo"
    try:
        info = probe(path)
    except ProcessingError:
        return "photo"
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    if ext in AUDIO_EXTS and has_audio:
        return "audio"
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video" and stream.get("disposition", {}).get("attached_pic") == 1:
            continue  # cover art inside an audio file
        if stream.get("codec_type") == "video":
            # A single-frame "video" (e.g. some PNGs) is a photo.
            frames = stream.get("nb_frames")
            duration = float(info.get("format", {}).get("duration") or 0)
            if frames == "1" or duration == 0:
                return "photo"
            return "video"
    if has_audio:
        return "audio"
    raise ProcessingError("No picture, video or audio found")


def fit_on_blurred_canvas(image):
    """Contain the image in 1920x1080 over a blurred, darkened cover of itself."""
    image = image.convert("RGB")
    cover_scale = max(TARGET_W / image.width, TARGET_H / image.height)
    cover = image.resize(
        (max(1, math.ceil(image.width * cover_scale)), max(1, math.ceil(image.height * cover_scale))),
        Image.LANCZOS,
    )
    left = (cover.width - TARGET_W) // 2
    top = (cover.height - TARGET_H) // 2
    background = cover.crop((left, top, left + TARGET_W, top + TARGET_H))
    background = background.filter(ImageFilter.GaussianBlur(40))
    background = ImageEnhance.Brightness(background).enhance(0.6)

    contain_scale = min(TARGET_W / image.width, TARGET_H / image.height)
    fg = image.resize(
        (max(1, round(image.width * contain_scale)), max(1, round(image.height * contain_scale))),
        Image.LANCZOS,
    )
    background.paste(fg, ((TARGET_W - fg.width) // 2, (TARGET_H - fg.height) // 2))
    return background


def process_photo(src, out_dir):
    with Image.open(src) as opened:
        image = ImageOps.exif_transpose(opened)
        image.load()
    canvas = fit_on_blurred_canvas(image)
    processed = out_dir / "photo.jpg"
    canvas.save(processed, "JPEG", quality=90, optimize=True)
    thumb = out_dir / "thumb.jpg"
    canvas.resize((THUMB_W, THUMB_H), Image.LANCZOS).save(thumb, "JPEG", quality=82)
    return {"processed": processed, "thumb": thumb, "width": TARGET_W, "height": TARGET_H, "duration_ms": 0}


def video_stream(info):
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video" and stream.get("disposition", {}).get("attached_pic") != 1:
            return stream
    return None


def audio_stream(info):
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "audio":
            return stream
    return None


def frame_rate(text):
    try:
        num, den = (text or "0/1").split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def rotation(stream):
    tags = stream.get("tags", {}) or {}
    if tags.get("rotate") not in (None, "0"):
        return True
    for side in stream.get("side_data_list", []) or []:
        if side.get("rotation") not in (None, 0, "0"):
            return True
    return False


def can_pass_through(info):
    """True when the video is already what vMix needs: H.264 1920x1080, 4:2:0,
    progressive, constant frame rate equal to the project's (or double it), not rotated.
    Then the picture is copied as is; only the audio may be converted."""
    video = video_stream(info)
    if video is None:
        return False
    fps = settings.VITRINE_VIDEO_FPS
    rate = frame_rate(video.get("r_frame_rate"))
    avg = frame_rate(video.get("avg_frame_rate"))
    checks = [
        video.get("codec_name") == "h264",
        int(video.get("width") or 0) == TARGET_W,
        int(video.get("height") or 0) == TARGET_H,
        video.get("pix_fmt") in ("yuv420p", "yuvj420p"),
        video.get("field_order") in (None, "progressive", "unknown"),
        not rotation(video),
        any(abs(rate - target) < 0.01 for target in (fps, fps * 2)),
        avg == 0 or abs(avg - rate) < 0.05,
    ]
    return all(checks)


def pass_through(src, processed, info):
    audio = audio_stream(info)
    audio_ok = audio is not None and audio.get("codec_name") == "aac" and str(audio.get("sample_rate")) == "48000"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy"]
    if audio_ok:
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    cmd += ["-movflags", "+faststart", str(processed)]
    run(cmd)


def process_video(src, out_dir):
    fps = settings.VITRINE_VIDEO_FPS
    processed = out_dir / "video.mp4"
    source_info = probe(src)
    if can_pass_through(source_info):
        logger.info("%s is already H.264 1080p at %s fps: copying without re-encoding", src, fps)
        pass_through(src, processed, source_info)
        return finish_video(processed, out_dir, passed_through=True)
    filter_graph = (
        f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},boxblur=40:4,eq=brightness=-0.12[bg];"
        f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={fps},format=yuv420p,setsar=1[v]"
    )
    run(
        [
            "ffmpeg", "-y", "-hide_banner", "-i", str(src),
            "-filter_complex", filter_graph,
            "-map", "[v]", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-crf", "20",
            "-g", str(fps * 2), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(processed),
        ]
    )
    return finish_video(processed, out_dir, passed_through=False)


def finish_video(processed, out_dir, passed_through):
    info = probe(processed)
    duration_ms = int(float(info.get("format", {}).get("duration") or 0) * 1000)

    thumb = out_dir / "thumb.jpg"
    seek = max(0.0, min(1.0, duration_ms / 3000.0))
    run(
        [
            "ffmpeg", "-y", "-hide_banner", "-ss", f"{seek:.2f}", "-i", str(processed),
            "-frames:v", "1", "-vf", f"scale={THUMB_W}:{THUMB_H}", "-q:v", "4", str(thumb),
        ]
    )

    filmstrip, count, cols, interval_ms = make_filmstrip(processed, out_dir, duration_ms)
    return {
        "passed_through": passed_through,
        "filmstrip_interval_ms": interval_ms,
        "processed": processed,
        "thumb": thumb,
        "filmstrip": filmstrip,
        "filmstrip_count": count,
        "filmstrip_cols": cols,
        "width": TARGET_W,
        "height": TARGET_H,
        "duration_ms": duration_ms,
    }


def make_filmstrip(video, out_dir, duration_ms):
    """Frames for the cue panel, tiled into one JPG sprite.

    One frame every VITRINE_FILMSTRIP_INTERVAL seconds, but never more than
    MAX_FRAMES: a 3-hour video gets a frame every 90 s instead. Each frame is
    taken with a fast seek, so long videos don't have to be decoded in full.
    Returns (path, count, columns, interval_ms).
    """
    duration_s = duration_ms / 1000
    interval = max(settings.VITRINE_FILMSTRIP_INTERVAL, math.ceil(duration_s / MAX_FRAMES))
    count = max(1, math.ceil(duration_s / interval))
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    frames = []
    for index in range(count):
        at = min(index * interval + interval / 2, max(0.0, duration_s - 0.5))
        target = frames_dir / f"f_{index:05d}.jpg"
        try:
            run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(video),
                    "-frames:v", "1", "-vf", f"scale={FRAME_W}:{FRAME_H}", "-q:v", "5", str(target),
                ]
            )
        except ProcessingError:
            pass
        if not target.exists():
            # Keep indexes aligned with time: reuse the previous frame (or black).
            if frames:
                target = frames[-1]
            else:
                Image.new("RGB", (FRAME_W, FRAME_H), (0, 0, 0)).save(target)
        frames.append(target)
    cols = min(FILMSTRIP_COLS, len(frames))
    rows = math.ceil(len(frames) / cols)
    sprite = Image.new("RGB", (cols * FRAME_W, rows * FRAME_H), (0, 0, 0))
    for index, frame_path in enumerate(frames):
        with Image.open(frame_path) as frame:
            sprite.paste(frame.convert("RGB"), ((index % cols) * FRAME_W, (index // cols) * FRAME_H))
    sprite_path = out_dir / "filmstrip.jpg"
    sprite.save(sprite_path, "JPEG", quality=78)
    return sprite_path, len(frames), cols, interval * 1000


def audio_card(title, subtitle, cover=None):
    """1920x1080 picture for an audio file: its cover art, or a title card."""
    if cover is not None:
        canvas = fit_on_blurred_canvas(cover)
    else:
        canvas = Image.new("RGB", (TARGET_W, TARGET_H), (24, 28, 38))
        draw = ImageDraw.Draw(canvas)
        for y in range(TARGET_H):
            shade = int(24 + 26 * y / TARGET_H)
            draw.line([(0, y), (TARGET_W, y)], fill=(shade, shade + 4, shade + 18))
        # A simple note symbol.
        cx, cy = TARGET_W // 2, 400
        draw.ellipse((cx - 150, cy + 40, cx - 30, cy + 130), fill=(232, 234, 238))
        draw.rectangle((cx - 44, cy - 160, cx - 30, cy + 90), fill=(232, 234, 238))
        draw.polygon([(cx - 44, cy - 160), (cx + 90, cy - 110), (cx + 90, cy - 60), (cx - 30, cy - 105)], fill=(232, 234, 238))
        try:
            big = ImageFont.load_default(size=72)
            small = ImageFont.load_default(size=44)
        except TypeError:  # very old Pillow
            big = small = ImageFont.load_default()
        title = title if len(title) <= 48 else title[:47] + "…"
        draw.text((TARGET_W // 2, 700), title, font=big, fill=(232, 234, 238), anchor="mm")
        draw.text((TARGET_W // 2, 790), subtitle, font=small, fill=(154, 161, 173), anchor="mm")
    return canvas


def process_audio(src, out_dir, original_name, owner):
    """Audio becomes an MP4: the sound (AAC 48 kHz) plus a still card (cover art
    or title) as picture. The agent either plays only the sound and keeps the
    default input on the projector, or shows the card, as set in its settings."""
    cover = None
    cover_path = out_dir / "cover.jpg"
    try:
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-an", "-map", "0:v:0",
             "-frames:v", "1", str(cover_path)])
        if cover_path.exists():
            with Image.open(cover_path) as opened:
                cover = opened.convert("RGB")
    except ProcessingError:
        cover = None
    title = Path(original_name).stem.replace("_", " ")
    card = audio_card(title, owner or "", cover)
    card_path = out_dir / "card.png"
    card.save(card_path)

    fps = settings.VITRINE_VIDEO_FPS
    processed = out_dir / "audio.mp4"
    run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loop", "1", "-framerate", str(fps), "-i", str(card_path),
            "-i", str(src), "-map", "0:v", "-map", "1:a:0", "-shortest",
            "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
            "-r", str(fps), "-g", str(fps * 10),
            "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(processed),
        ]
    )
    info = probe(processed)
    duration_ms = int(float(info.get("format", {}).get("duration") or 0) * 1000)
    thumb = out_dir / "thumb.jpg"
    card.resize((THUMB_W, THUMB_H), Image.LANCZOS).save(thumb, "JPEG", quality=82)
    return {"processed": processed, "thumb": thumb, "width": TARGET_W, "height": TARGET_H, "duration_ms": duration_ms}


def process_file(src, original_name, owner=""):
    """Process one upload. Returns a dict with the produced files (in a temp dir)."""
    src = Path(src)
    kind = detect_kind(src, original_name)
    out_dir = Path(tempfile.mkdtemp(prefix="vitrine-"))
    try:
        if kind == "photo":
            result = process_photo(src, out_dir)
        elif kind == "audio":
            result = process_audio(src, out_dir, original_name, owner)
        else:
            result = process_video(src, out_dir)
    except Exception:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    result["kind"] = kind
    result["out_dir"] = out_dir
    result["sha256"] = sha256_of(result["processed"])
    result["size"] = result["processed"].stat().st_size
    return result
