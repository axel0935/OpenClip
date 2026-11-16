"""Crop geometry: 9:16 window, dead zone, pan speed limit and simplification."""

from dataclasses import dataclass

from openclip.core.models import CropKeyframe, CropPath, MediaInfo

_DEAD_ZONE_RATIO = 0.08  # of crop width, each side
_MAX_PAN_RATIO = 0.35  # crop widths per second
_SMOOTHING = 0.35  # low-pass blend toward the target
_MAX_KEYFRAMES_PER_MIN = 40
_RDP_EPSILON_RATIO = 0.015  # of crop width


@dataclass(frozen=True)
class SubjectSample:
    """One subject-centre observation in source pixels."""

    t: float
    cx: float | None
    is_cut: bool = False


def crop_window(media: MediaInfo) -> tuple[int, int] | None:
    """Return ``(w, h)`` of the 9:16 crop, or ``None`` for full-frame sources."""
    width, height = media.width, media.height
    if width <= 0 or height <= 0:
        return None
    if width / height <= 9 / 16:
        return None  # already vertical or taller than wide
    h = height - (height % 2)
    w = int(round(h * 9 / 16))
    w -= w % 2
    if w <= 0 or w >= width:
        return None
    return w, h


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def smooth_center_path(
    samples: list[SubjectSample],
    crop_w: int,
    source_w: int,
    source_h: int,
    clip_duration: float,
) -> list[tuple[float, float]]:
    """Track the subject centre through dead zone, smoothing and pan limits.

    Returns ``(t, x_left)`` pairs in source pixels. On scene cuts and after
    dropouts the centre snaps instead of panning.
    """
    max_left = source_w - crop_w
    center_x = source_w / 2
    dead = crop_w * _DEAD_ZONE_RATIO
    max_step = crop_w * _MAX_PAN_RATIO / 4.0  # per sample at 4 fps
    points: list[tuple[float, float]] = []
    last_t = 0.0
    for sample in samples:
        dt = max(0.05, sample.t - last_t) if points else 0.25
        last_t = sample.t
        if sample.cx is None:
            points.append((sample.t, _clamp(center_x - crop_w / 2, 0, max_left)))
            continue
        target_center = _clamp(sample.cx, crop_w / 2, source_w - crop_w / 2)
        if sample.is_cut:
            center_x = target_center
        else:
            delta = target_center - center_x
            if abs(delta) > dead:
                step = _clamp(delta, -max_step * (dt * 4), max_step * (dt * 4))
                desired = center_x + step
            else:
                desired = center_x
            center_x = center_x * (1 - _SMOOTHING) + desired * _SMOOTHING
        x_left = _clamp(center_x - crop_w / 2, 0, max_left)
        points.append((sample.t, x_left))
    # Guarantee coverage of t=0 and the clip end.
    if not points or points[0][0] > 0:
        points.insert(0, (0.0, points[0][1] if points else max_left / 2))
    if points[-1][0] < clip_duration:
        points.append((clip_duration, points[-1][1]))
    return points


def _rdp(points: list[tuple[float, float]], epsilon: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker simplification of a polyline."""
    if len(points) < 3:
        return points

    def _perp(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return float(((px - ax) ** 2 + (py - ay) ** 2) ** 0.5)
        return abs(dy * px - dx * py + bx * ay - by * ax) / (dx * dx + dy * dy) ** 0.5

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        ax, ay = points[i0]
        bx, by = points[i1]
        dmax, imax = -1.0, -1
        for i in range(i0 + 1, i1):
            d = _perp(points[i][0], points[i][1], ax, ay, bx, by)
            if d > dmax:
                dmax, imax = d, i
        if dmax > epsilon:
            keep[imax] = True
            stack.append((i0, imax))
            stack.append((imax, i1))
    return [p for p, k in zip(points, keep, strict=True) if k]


def simplify_keyframes(
    points: list[tuple[float, float]],
    crop_w: int,
    source_w: int,
    source_h: int,
    clip_duration: float,
) -> list[CropKeyframe]:
    """Simplify the centre path into CropKeyframes within the rate budget."""
    if not points:
        return []
    max_count = max(2, int(clip_duration / 60 * _MAX_KEYFRAMES_PER_MIN) + 1)
    epsilon = crop_w * _RDP_EPSILON_RATIO
    simplified = _rdp(points, epsilon)
    # If still over budget, widen epsilon until it fits.
    while len(simplified) > max_count and epsilon < crop_w * 0.2:
        epsilon *= 1.8
        simplified = _rdp(points, epsilon)
    if len(simplified) > max_count:
        # Last resort: uniform subsample keeping endpoints.
        step = (len(simplified) - 1) / (max_count - 1)
        simplified = [simplified[round(i * step)] for i in range(max_count)]
    # Guarantee coverage of t=0 and the clip end.
    if simplified[0][0] > 0:
        simplified.insert(0, (0.0, simplified[0][1]))
    if simplified[-1][0] < clip_duration:
        simplified.append((clip_duration, simplified[-1][1]))
    max_left = source_w - crop_w
    return [
        CropKeyframe(
            t=round(t, 3),
            x=int(round(_clamp(x, 0, max_left))),
            y=0,
            w=crop_w,
            h=source_h,
        )
        for t, x in simplified
    ]


def build_crop_path(
    media: MediaInfo,
    clip_duration: float,
    samples: list[SubjectSample],
) -> CropPath:
    """Assemble the final :class:`CropPath` for a ``fill`` layout."""
    window = crop_window(media)
    if window is None:
        h = media.height - (media.height % 2)
        return CropPath(
            layout="fill",
            source_width=media.width,
            source_height=media.height,
            keyframes=[CropKeyframe(t=0.0, x=0, y=0, w=media.width, h=h)],
        )
    crop_w, _ = window
    points = smooth_center_path(samples, crop_w, media.width, media.height, clip_duration)
    keyframes = simplify_keyframes(points, crop_w, media.width, media.height, clip_duration)
    if not keyframes:
        keyframes = [
            CropKeyframe(t=0.0, x=(media.width - crop_w) // 2, y=0, w=crop_w, h=media.height)
        ]
    return CropPath(
        layout="fill",
        source_width=media.width,
        source_height=media.height,
        keyframes=keyframes,
    )


def fit_crop_path(media: MediaInfo, clip_duration: float) -> CropPath:
    """Return the ``fit`` layout: one full-frame keyframe."""
    return CropPath(
        layout="fit",
        source_width=media.width,
        source_height=media.height,
        keyframes=[CropKeyframe(t=0.0, x=0, y=0, w=media.width, h=media.height)],
    )
