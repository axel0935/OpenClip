"""FFmpeg preview rendering and contact-sheet assembly for caption styles.

Everything here calls FFmpeg through an argument list; no shell is involved.
The preview keeps a centred 9:16 crop scaled to the 1080x1920 canvas that the
ASS file was authored for, so glyph sizes match the final render exactly.
"""

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from openclip.captions.chunks import build_chunks, clip_words
from openclip.captions.fonts import ffmpeg_subtitles_filter, fonts_dir_or_none
from openclip.captions.text import CANVAS_HEIGHT, CANVAS_WIDTH
from openclip.captions.timecode import ass_time, to_centiseconds
from openclip.core.errors import RenderError
from openclip.core.models import CaptionStyle, Clip, Transcript

LOGGER = logging.getLogger(__name__)

#: Duration of the per-style preview clip, in seconds.
PREVIEW_SECONDS = 6.0
#: Longest preview produced by ``--preview``, in seconds.
MAX_PREVIEW_SECONDS = 10.0
#: Canvas the preview is rendered at.
OUTPUT_WIDTH = CANVAS_WIDTH
OUTPUT_HEIGHT = CANVAS_HEIGHT
#: Seconds before the sample point that keep entry animations settled.
PREROLL = 0.45
#: Frame rate used by previews, and therefore by the trim frame count.
FRAME_RATE = 30
#: Neutral backdrop for contact-sheet frames.
#:
#: A style sheet must show the preset, not the sample. Pale caption colours
#: (``karaoke-glow`` is nearly white) vanish over pale footage, so sheet frames
#: are rendered over mid-grey; the MP4 previews still use the real footage.
SHEET_BACKGROUND = "0x3A3D42"
#: Seconds to wait inside a chunk before sampling, clearing entry animations.
ENTRY_SETTLE = 0.32
#: Seconds before the chunk ends that the sample avoids, clearing exit fades.
EXIT_GUARD = 0.14

_CROP = (
    "crop="
    "w='if(gt(iw/ih,9/16),ih*9/16,iw)':"
    "h='if(gt(iw/ih,9/16),ih,iw*16/9)':"
    "x='(iw-ow)/2':y='(ih-oh)/2'"
)
_SCALE = f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:flags=bicubic"


@dataclass(frozen=True)
class PreviewResult:
    """Paths produced by one preview render."""

    video: Path
    frame: Path | None = None


def _ffmpeg_binary(ffmpeg: str | None) -> str:
    """Return an executable FFmpeg path or raise a clear error."""
    binary = ffmpeg or "ffmpeg"
    if shutil.which(binary) is None and not Path(binary).is_file():
        raise RenderError(f"FFmpeg '{binary}' was not found. Install FFmpeg to render previews.")
    return binary


def _run(cmd: list[str]) -> None:
    """Run one FFmpeg command and translate failures into :class:`RenderError`."""
    LOGGER.debug("Running %s", cmd)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=900)
    except FileNotFoundError as exc:
        raise RenderError(
            "FFmpeg could not be executed. Install FFmpeg to render previews."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderError("FFmpeg timed out while rendering the caption preview.") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "unknown error"
        raise RenderError(f"FFmpeg failed to render the caption preview: {detail}")


def _video_filter(ass_path: Path, fonts: Path | None) -> str:
    """Compose the crop, scale and subtitle filters."""
    return f"{_CROP},{_SCALE},{ffmpeg_subtitles_filter(ass_path, fonts)}"


def _seek_and_shift(ass_path: Path, offset: float, workdir: Path) -> Path:
    """Prepare the ASS file for an input seek of ``offset`` seconds.

    ``ffmpeg -ss`` resets the filter clock to zero, so subtitle timings must be
    shifted by the same amount to stay in sync with the picture.
    """
    if offset <= 0:
        return ass_path
    shifted = workdir / "shifted.ass"
    shift_ass(ass_path, shifted, -to_centiseconds(offset))
    return shifted


def shift_ass(ass_path: Path, out_path: Path, shift_cs: int) -> Path:
    """Write a copy of an ASS file with every dialogue timing shifted by ``shift_cs``.

    Events that end at or before the new zero point are dropped instead of being
    clamped into a tiny window, which would make them flash after a seek. Events
    that straddle the new zero start at zero.
    """
    lines: list[str] = []
    for line in ass_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            lines.append(line)
            continue
        head, _, rest = line.partition(": ")
        fields = rest.split(",", 9)
        raw_start = _parse_ass_time(fields[1])
        raw_end = _parse_ass_time(fields[2])
        start = raw_start + shift_cs
        end = raw_end + shift_cs
        if end <= 0 or start >= end:
            continue
        if end <= start:
            continue
        fields[1] = ass_time(max(0, start))
        fields[2] = ass_time(end)
        lines.append(f"{head}: {','.join(fields)}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def render_preview(
    video: Path,
    clip: Clip,
    ass_path: Path,
    out_path: Path,
    *,
    seconds: float = PREVIEW_SECONDS,
    start_offset: float = 0.0,
    ffmpeg: str | None = None,
) -> Path:
    """Render ``seconds`` of ``clip`` with burned-in captions at 1080x1920.

    ``start_offset`` seeks inside the clip; the ASS file keeps clip-relative
    timings and is shifted into a temporary copy for the seek.
    """
    binary = _ffmpeg_binary(ffmpeg)
    if not video.is_file():
        raise RenderError(f"Source video {video} does not exist.")
    if not ass_path.is_file():
        raise RenderError(f"Subtitle file {ass_path} does not exist.")
    offset = max(0.0, min(start_offset, max(0.0, clip.end - clip.start)))
    duration = max(0.1, min(seconds, clip.end - clip.start - offset, MAX_PREVIEW_SECONDS))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openclip-preview-") as scratch:
        ass_for_render = _seek_and_shift(ass_path, offset, Path(scratch))
        _run(
            [
                binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-ss",
                f"{clip.start + offset:.3f}",
                "-t",
                f"{duration:.3f}",
                "-i",
                str(video),
                "-vf",
                _video_filter(ass_for_render, fonts_dir_or_none()),
                "-r",
                "30",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                str(out_path),
            ]
        )
    return out_path


def _parse_ass_time(value: str) -> int:
    """Parse an ASS ``H:MM:SS.cc`` timestamp into centiseconds."""
    hours, minutes, seconds = value.strip().split(":")
    whole, _, hundredths = seconds.partition(".")
    return (
        int(hours) * 360_000
        + int(minutes) * 6_000
        + int(whole) * 100
        + int(hundredths.ljust(2, "0")[:2] or 0)
    )


def render_frame(
    video: Path,
    clip: Clip,
    ass_path: Path,
    out_path: Path,
    *,
    at: float,
    ffmpeg: str | None = None,
    background: str | None = None,
) -> Path:
    """Write one PNG frame ``at`` seconds into the clip, with captions burned in.

    Subtitle times are shifted by the input seek, so libass' clock matches the
    trimmed picture. The remaining offset is applied as an *output* seek (``-ss``
    after ``-i``): an output seek drops frames instead of resetting the subtitle
    clock, while a ``trim`` filter would restart ``\\fad`` at alpha zero and
    capture a fully transparent frame.

    ``background`` replaces the source image with a flat colour while keeping
    the subtitles, which is what a style contact sheet wants: pale caption
    colours are unreadable over pale footage, and the sheet must show the style
    rather than the sample.
    """
    binary = _ffmpeg_binary(ffmpeg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    limit = max(0.0, clip.end - clip.start)
    offset = max(0.0, min(at, limit))
    preroll = min(PREROLL, offset)
    seek = offset - preroll
    with tempfile.TemporaryDirectory(prefix="openclip-frame-") as scratch:
        ass_for_render = _seek_and_shift(ass_path, seek, Path(scratch))
        subtitles = ffmpeg_subtitles_filter(ass_for_render, fonts_dir_or_none())
        if background:
            clip_colour = (
                f"color=c={background}:s={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}"
                f":r={FRAME_RATE}:d={PREROLL + 0.5:.2f}"
            )
            source = ["-f", "lavfi", "-i", clip_colour]
            graph = f"format=rgba,{subtitles}"
        else:
            source = ["-ss", f"{clip.start + seek:.3f}", "-i", str(video)]
            graph = _video_filter(ass_for_render, fonts_dir_or_none())
        cmd = [
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            *source,
            "-vf",
            graph,
        ]
        if preroll > 0:
            cmd.extend(["-ss", f"{preroll:.3f}"])
        cmd.extend(["-frames:v", "1", "-update", "1", str(out_path)])
        _run(cmd)
    return out_path


def sample_time(transcript: Transcript, clip: Clip, style: CaptionStyle) -> float:
    """Pick a clip-relative moment with a steady, multi-word caption on screen.

    The moment is chosen after any entry animation has finished and before any
    exit fade starts, so the contact sheet shows the settled style rather than
    a half-faded frame.
    """
    chunks = build_chunks(clip_words(transcript, clip), style, clip.end - clip.start)
    if not chunks:
        return min(1.0, max(0.0, clip.end - clip.start))
    with_text = [chunk for chunk in chunks if len(chunk.words) >= 2] or chunks
    chosen = with_text[min(len(with_text) - 1, max(0, len(with_text) // 2))]
    span = chosen.end - chosen.start
    lead = min(max(ENTRY_SETTLE, 0.0), span * 0.45)
    tail = min(EXIT_GUARD, span * 0.25)
    return chosen.start + lead + max(0.0, (span - lead - tail)) * 0.5


def contact_sheet(
    entries: list[tuple[str, Path]],
    out_path: Path,
    *,
    columns: int = 4,
    tile_width: int = 270,
) -> Path:
    """Assemble labelled style frames into one PNG contact sheet."""
    if not entries:
        raise RenderError("Cannot build a contact sheet without frames.")
    tile_height = round(tile_width * OUTPUT_HEIGHT / OUTPUT_WIDTH)
    label_height = 44
    rows = (len(entries) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * tile_width, rows * (tile_height + label_height)), "#101014")
    draw = ImageDraw.Draw(sheet)
    font = _label_font(18)
    for index, (label, path) in enumerate(entries):
        column, row = index % columns, index // columns
        x = column * tile_width
        y = row * (tile_height + label_height)
        with Image.open(path) as frame:
            thumb = frame.convert("RGB").resize((tile_width, tile_height), Image.Resampling.LANCZOS)
        sheet.paste(thumb, (x, y))
        draw.rectangle(
            [x, y + tile_height, x + tile_width, y + tile_height + label_height], fill="#101014"
        )
        draw.text((x + 10, y + tile_height + 12), label, fill="#F7F7F5", font=font)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, format="PNG", optimize=True)
    return out_path


def _label_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a readable label font, falling back to Pillow's bitmap default."""
    fonts = fonts_dir_or_none()
    candidates = [fonts / "Inter" / "Inter-Bold.ttf"] if fonts is not None else []
    candidates.extend([Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")])
    for candidate in candidates:
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:
                continue
    return ImageFont.load_default()
