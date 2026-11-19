"""Turn whatever a phone uploads into files vMix plays reliably.

Photos: HEIC/JPG/PNG -> 1920x1080 JPG, EXIF rotation applied, portrait shots
on a blurred copy of themselves instead of black bars.
Videos: anything ffmpeg reads -> H.264 High 1080p, constant frame rate, AAC
48 kHz stereo, faststart; same blurred fill for vertical video.
Both get a thumbnail; videos also get a filmstrip sprite for the cue panel.
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
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

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

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


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
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            # A single-frame "video" (e.g. some PNGs) is a photo.
            frames = stream.get("nb_frames")
            duration = float(info.get("format", {}).get("duration") or 0)
            if frames == "1" or duration == 0:
                return "photo"
            return "video"
    raise ProcessingError("No picture or video stream found")


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


def process_video(src, out_dir):
    fps = settings.VITRINE_VIDEO_FPS
    processed = out_dir / "video.mp4"
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

    filmstrip, count, cols = make_filmstrip(processed, out_dir, duration_ms)
    return {
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
    """One frame every N seconds, tiled into a single JPG sprite."""
    interval = settings.VITRINE_FILMSTRIP_INTERVAL
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    run(
        [
            "ffmpeg", "-y", "-hide_banner", "-i", str(video),
            "-vf", f"fps=1/{interval},scale={FRAME_W}:{FRAME_H}", "-q:v", "5",
            str(frames_dir / "f_%05d.jpg"),
        ]
    )
    frames = sorted(frames_dir.glob("f_*.jpg"))
    # ffmpeg's fps filter samples the middle of each interval; keep the count
    # consistent with the duration so the cue panel maps index -> time.
    expected = max(1, math.ceil(duration_ms / 1000 / interval))
    frames = frames[:expected]
    if not frames:
        return None, 0, 0
    cols = min(FILMSTRIP_COLS, len(frames))
    rows = math.ceil(len(frames) / cols)
    sprite = Image.new("RGB", (cols * FRAME_W, rows * FRAME_H), (0, 0, 0))
    for index, frame_path in enumerate(frames):
        with Image.open(frame_path) as frame:
            sprite.paste(frame.convert("RGB"), ((index % cols) * FRAME_W, (index // cols) * FRAME_H))
    sprite_path = out_dir / "filmstrip.jpg"
    sprite.save(sprite_path, "JPEG", quality=78)
    return sprite_path, len(frames), cols


def process_file(src, original_name):
    """Process one upload. Returns a dict with the produced files (in a temp dir)."""
    src = Path(src)
    kind = detect_kind(src, original_name)
    out_dir = Path(tempfile.mkdtemp(prefix="vitrine-"))
    try:
        if kind == "photo":
            result = process_photo(src, out_dir)
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
