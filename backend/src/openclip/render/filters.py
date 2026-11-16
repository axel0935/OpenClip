"""Pure filter-graph builders for the render stage (unit-testable)."""

from openclip.core.models import CropKeyframe, CropPath, MediaInfo

CANVAS_W, CANVAS_H = 1080, 1920
MAX_EXPR_KEYFRAMES = 20


def crop_x_expression(keyframes: list[CropKeyframe]) -> str:
    """Build a piecewise-linear ``x(t)`` expression for the crop filter.

    ``t`` is clip-relative seconds (the graph applies ``setpts=PTS-STARTPTS``
    first). Keyframes are uniformly resampled to at most
    :data:`MAX_EXPR_KEYFRAMES` so the expression stays parser-friendly; the
    pan speed limit makes the loss imperceptible.
    """
    kfs = sorted(keyframes, key=lambda k: k.t)
    if len(kfs) > MAX_EXPR_KEYFRAMES:
        step = (len(kfs) - 1) / (MAX_EXPR_KEYFRAMES - 1)
        kfs = [kfs[round(i * step)] for i in range(MAX_EXPR_KEYFRAMES)]
    if not kfs:
        return "0"
    if len(kfs) == 1:
        return str(kfs[0].x)

    def lerp(a: CropKeyframe, b: CropKeyframe) -> str:
        dt = b.t - a.t
        if dt <= 0:
            return str(b.x)
        frac = f"(t-{a.t:.3f})/{dt:.3f}"
        return f"({a.x}+({b.x - a.x})*{frac})"

    # Innermost value: the last keyframe's x for t >= last.t. Each enclosing
    # ``if`` covers one segment [a.t, b.t) with linear interpolation.
    expr = str(kfs[-1].x)
    for a, b in zip(reversed(kfs[:-1]), reversed(kfs[1:]), strict=True):
        expr = f"if(lt(t,{b.t:.3f}),{lerp(a, b)},{expr})"
    return expr


def _fit_graph() -> str:
    """Full frame over a blurred, zoomed copy of itself (1080x1920)."""
    return (
        "split=2[bg][fg];"
        "[bg]scale=270:480:force_original_aspect_ratio=increase,"
        "crop=270:480,gblur=sigma=12,scale=1080:1920[bg2];"
        "[fg]scale=1080:-2[fg2];"
        "[bg2][fg2]overlay=(W-w)/2:(H-h)/2"
    )


def video_filter_chain(
    crop: CropPath,
    media: MediaInfo,
    ass_filter: str | None,
    out_fps: float,
) -> str:
    """Return the ``-vf`` chain producing the final 1080x1920 RGB frames."""
    parts: list[str] = ["setpts=PTS-STARTPTS"]
    if crop.layout == "fill":
        kf0 = crop.keyframes[0]
        crop_w, crop_h = kf0.w, kf0.h
        x_expr = crop_x_expression(crop.keyframes)
        parts.append(f"crop={crop_w}:{crop_h}:x='{x_expr}':y=0")
        parts.append(f"scale={CANVAS_W}:{CANVAS_H}")
    else:
        parts.append(_fit_graph())
    parts.append(f"fps={out_fps:.6g}")
    if ass_filter:
        parts.append(ass_filter)
    parts.append("format=yuv420p")
    return ",".join(parts)


def preview_filter_chain(crop: CropPath, media: MediaInfo, out_w: int, out_h: int) -> str:
    """Captionless low-res variant of the video chain for the proxy preview."""
    if crop.layout == "fill":
        kf0 = crop.keyframes[0]
        x_expr = crop_x_expression(crop.keyframes)
        return (
            f"setpts=PTS-STARTPTS,"
            f"crop={kf0.w}:{kf0.h}:x='{x_expr}':y=0,"
            f"scale={out_w}:{out_h},fps=30,format=yuv420p"
        )
    return (
        "setpts=PTS-STARTPTS,"
        f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
        f"crop={out_w}:{out_h},fps=30,format=yuv420p"
    )


def escape_drawtext_path(path: str) -> str:
    """Escape a filesystem path for use inside a filter option value."""
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
