import { useState, useMemo, useRef, useCallback, useEffect } from "react";
import {
  Download,
  Plus,
  Play,
  Copy,
  Check,
  ChevronDown,
  Sparkles,
  Scissors,
  Clock,
  RotateCcw,
  Eye,
  EyeOff,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import { useToast } from "@/components/ui/use-toast";
import type { CaptionStyle, Clip, ClipUpdate, Job } from "@/api/client";
import { defaultApiClient, jobTitle } from "@/api/client";
import { ApiError } from "@/api/httpClient";

interface ResultsScreenProps {
  job: Job;
  captionStyles: CaptionStyle[];
  /** Per-clip render version bumped on every clip_ready SSE event. */
  clipVersions?: Record<string, number>;
  onNewVideo: () => void;
  onUpdateClip: (clipId: string, update: ClipUpdate) => Promise<Clip>;
}

type SortOption = "score_desc" | "duration_desc" | "duration_asc" | "source_order";

export function ResultsScreen({
  job,
  captionStyles,
  clipVersions,
  onNewVideo,
  onUpdateClip,
}: ResultsScreenProps) {
  const { toast } = useToast();
  const [sortOption, setSortOption] = useState<SortOption>("score_desc");
  const [selectedClip, setSelectedClip] = useState<Clip | null>(null);

  // Detail Sheet state
  const [trimStart, setTrimStart] = useState<number>(0);
  const [trimEnd, setTrimEnd] = useState<number>(0);
  const [selectedStyleId, setSelectedStyleId] = useState<string>(
    job.options?.caption_style_id || "clean-pop",
  );
  // Re-render state: the clip id currently re-rendering (null = idle) plus the
  // clipVersions value at PATCH time, so the clip_ready SSE event that follows
  // the background render can be detected.
  const [rerenderingClipId, setRerenderingClipId] = useState<string | null>(null);
  const [rerenderBaseVersion, setRerenderBaseVersion] = useState(0);
  const rerenderTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Cache-busting tokens: local bumps plus server clip_versions from SSE.
  const [mediaCacheBust, setMediaCacheBust] = useState<Record<string, number>>({});

  // Player source: final render by default, toggle to the low-res preview.
  const [usePreview, setUsePreview] = useState(false);

  const videoRef = useRef<HTMLVideoElement>(null);

  // Download all state
  const [isDownloadingZip, setIsDownloadingZip] = useState(false);

  const clips = useMemo(() => job.clips || [], [job.clips]);

  const cacheBust = useCallback(
    (clipId: string) => {
      const version = clipVersions?.[clipId] ?? 0;
      const local = mediaCacheBust[clipId] ?? 0;
      return version || local ? `?v=${version}-${local}` : "";
    },
    [clipVersions, mediaCacheBust],
  );

  const isReRendering =
    rerenderingClipId !== null && rerenderingClipId === selectedClip?.id;

  const handleSelectClip = (clip: Clip) => {
    setSelectedClip(clip);
    setTrimStart(clip.start);
    setTrimEnd(clip.end);
    setUsePreview(false);
  };

  // Sorting
  const sortedClips = useMemo(() => {
    const list = [...clips];
    switch (sortOption) {
      case "score_desc":
        return list.sort((a, b) => b.score - a.score);
      case "duration_desc":
        return list.sort((a, b) => b.end - b.start - (a.end - a.start));
      case "duration_asc":
        return list.sort((a, b) => a.end - a.start - (b.end - b.start));
      case "source_order":
        return list.sort((a, b) => a.start - b.start);
      default:
        return list;
    }
  }, [clips, sortOption]);

  const totalDurationSaved = useMemo(() => {
    const sourceDuration = job.media?.duration || 3600;
    const clipsDuration = clips.reduce((acc, c) => acc + (c.end - c.start), 0);
    const saved = Math.max(0, sourceDuration - clipsDuration);
    return Math.round(saved / 60);
  }, [clips, job.media?.duration]);

  const formatDuration = (start: number, end: number): string => {
    const totalSec = Math.max(0, Math.round(end - start));
    const mins = Math.floor(totalSec / 60);
    const secs = totalSec % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  const getScoreBadge = (score: number) => {
    if (score >= 70) return { variant: "scoreHigh" as const, tier: "Strong" };
    if (score >= 40) return { variant: "scoreMedium" as const, tier: "Good" };
    return { variant: "scoreLow" as const, tier: "Fair" };
  };

  const minClip = job.options?.min_clip_seconds ?? 20;
  const maxClip = job.options?.max_clip_seconds ?? 90;

  const handleNudgeStart = (delta: number) => {
    setTrimStart((s) => {
      const next = Math.max(0, Math.round((s + delta) * 10) / 10);
      if (next >= trimEnd - minClip) return s;
      return next;
    });
  };

  const handleNudgeEnd = (delta: number) => {
    setTrimEnd((e) => {
      const maxLimit = job.media?.duration || 3600;
      const next = Math.min(maxLimit, Math.round((e + delta) * 10) / 10);
      if (next <= trimStart + minClip) return e;
      return next;
    });
  };

  const currentClipDuration = Math.round((trimEnd - trimStart) * 10) / 10;
  const isShortWarning = currentClipDuration < minClip;
  const isLongWarning = currentClipDuration > maxClip;

  // Finish the re-render spinner when the clip_ready SSE event bumps this
  // clip's version past the value captured at PATCH time.
  useEffect(() => {
    if (rerenderingClipId === null) return;
    const version = clipVersions?.[rerenderingClipId] ?? 0;
    if (version <= rerenderBaseVersion) return;
    if (rerenderTimeoutRef.current) {
      clearTimeout(rerenderTimeoutRef.current);
      rerenderTimeoutRef.current = null;
    }
    setRerenderingClipId(null);
    const live = clips.find((c) => c.id === rerenderingClipId);
    if (live) {
      setSelectedClip(live);
      setTrimStart(live.start);
      setTrimEnd(live.end);
    }
    toast("Clip re-rendered");
  }, [clipVersions, rerenderingClipId, rerenderBaseVersion, clips, toast]);

  // Fallback when the SSE clip_ready never arrives: after 2 minutes assume the
  // render finished and bust the media cache so revalidation picks it up.
  useEffect(() => {
    return () => {
      if (rerenderTimeoutRef.current) clearTimeout(rerenderTimeoutRef.current);
    };
  }, []);

  // Keep the open clip in sync when job.clips changes (clip_ready upserts).
  const liveClip = useMemo(
    () => clips.find((c) => c.id === selectedClip?.id) ?? null,
    [clips, selectedClip?.id],
  );
  useEffect(() => {
    if (!selectedClip || !liveClip || liveClip === selectedClip || isReRendering) {
      return;
    }
    const trimsDirty =
      Math.abs(trimStart - selectedClip.start) > 1e-6 ||
      Math.abs(trimEnd - selectedClip.end) > 1e-6;
    setSelectedClip(liveClip);
    if (!trimsDirty) {
      setTrimStart(liveClip.start);
      setTrimEnd(liveClip.end);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveClip]);

  const handleReRender = async () => {
    if (!selectedClip || isReRendering) return;
    const clipId = selectedClip.id;
    setRerenderingClipId(clipId);
    setRerenderBaseVersion(clipVersions?.[clipId] ?? 0);

    rerenderTimeoutRef.current = setTimeout(() => {
      rerenderTimeoutRef.current = null;
      setRerenderingClipId((cur) => (cur === clipId ? null : cur));
      setMediaCacheBust((prev) => ({ ...prev, [clipId]: Date.now() }));
      toast("Re-render is taking longer than expected — reload to check", "warning");
    }, 120_000);

    try {
      const updated = await onUpdateClip(clipId, {
        start: trimStart,
        end: trimEnd,
        caption_style_id: selectedStyleId,
      });
      setSelectedClip(updated);
      setTrimStart(updated.start);
      setTrimEnd(updated.end);
      // Spinner stays until the clip_ready SSE event (or the 2 min fallback).
    } catch (err) {
      if (rerenderTimeoutRef.current) {
        clearTimeout(rerenderTimeoutRef.current);
        rerenderTimeoutRef.current = null;
      }
      setRerenderingClipId(null);
      toast(err instanceof ApiError ? err.message : "Failed to re-render clip", "destructive");
    }
  };

  const handleDownloadZip = () => {
    setIsDownloadingZip(true);
    const url = defaultApiClient.getDownloadZipUrl(job.id);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${job.id}-clips.zip`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => {
      setIsDownloadingZip(false);
      toast(`${clips.length} clips downloading as ZIP`);
    }, 500);
  };

  const handleDownloadMp4 = (clip: Clip) => {
    const url = `${defaultApiClient.getVideoUrl(job.id, clip.id)}${cacheBust(clip.id)}`;
    const a = document.createElement("a");
    a.href = url;
    const slug = clip.title.replace(/[^\w-]+/g, "-").replace(/^-+|-+$/g, "") || clip.id;
    a.download = `${slug}.mp4`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const copyToClipboard = (text: string, message: string) => {
    navigator.clipboard.writeText(text).catch(() => null);
    toast(message);
  };

  const activeStyle = captionStyles.find((s) => s.id === selectedStyleId) || captionStyles[0];

  // Live region announcement for progress (every 5 s max is handled by the backend SSE rate)
  const progressPct = Math.round((job.progress ?? 0) * 100);

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col px-4 py-6 md:px-8">
      {/* Hidden live region for screen reader progress */}
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {clips.length > 0 &&
          `${clips.length} ${clips.length === 1 ? "clip" : "clips"} ready. ${progressPct}% complete.`}
      </div>

      {/* Top Header bar */}
      <div className="flex flex-col gap-4 border-b border-border pb-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="truncate text-xl font-bold text-foreground max-w-lg md:text-2xl">
            {jobTitle(job)}
          </h1>
          <p className="text-xs text-muted-foreground mt-1">
            {clips.length} {clips.length === 1 ? "clip" : "clips"}
            {totalDurationSaved > 0 && ` · ${totalDurationSaved} min saved`}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          {/* Sort Selector */}
          <div className="relative inline-flex items-center">
            <select
              value={sortOption}
              onChange={(e) => setSortOption(e.target.value as SortOption)}
              aria-label="Sort clips"
              className="h-9 rounded-lg border border-input bg-card pl-3 pr-8 text-xs font-medium text-foreground focus:outline-none focus:ring-1 focus:ring-ring appearance-none cursor-pointer"
            >
              <option value="score_desc">Score ↓</option>
              <option value="duration_desc">Longest</option>
              <option value="duration_asc">Shortest</option>
              <option value="source_order">Source order</option>
            </select>
            <ChevronDown className="pointer-events-none absolute right-2.5 h-3.5 w-3.5 text-muted-foreground" />
          </div>

          {/* Download all zip */}
          <Button
            variant="outline"
            size="sm"
            onClick={handleDownloadZip}
            disabled={isDownloadingZip}
            className="text-xs font-semibold gap-1.5 h-9"
          >
            <Download className="h-3.5 w-3.5" />
            {isDownloadingZip ? "Downloading…" : "Download all (.zip)"}
          </Button>

          {/* New Video button */}
          <Button
            size="sm"
            onClick={onNewVideo}
            className="text-xs font-semibold gap-1.5 h-9 bg-primary text-primary-foreground"
          >
            <Plus className="h-3.5 w-3.5" />
            New video
          </Button>
        </div>
      </div>

      {/* 9:16 Responsive Grid */}
      <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
        {sortedClips.map((clip) => {
          const badge = getScoreBadge(clip.score);
          const durationStr = formatDuration(clip.start, clip.end);
          const thumbUrl = `${defaultApiClient.getThumbnailUrl(job.id, clip.id)}${cacheBust(clip.id)}`;

          return (
            <div
              key={clip.id}
              role="button"
              tabIndex={0}
              onClick={() => handleSelectClip(clip)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  handleSelectClip(clip);
                }
              }}
              className="group relative flex flex-col overflow-hidden rounded-2xl border border-border bg-card p-3 text-left transition-all duration-200 hover:border-foreground/30 hover:shadow-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-ring cursor-pointer"
            >
              {/* 9:16 Aspect ratio video thumbnail */}
              <div className="relative aspect-[9/16] w-full overflow-hidden rounded-xl bg-black">
                <img
                  src={thumbUrl}
                  alt={clip.title}
                  className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                  onError={(e) => {
                    // Fall back to a simple placeholder on 404
                    (e.target as HTMLImageElement).style.display = "none";
                  }}
                />

                {/* Badges Overlay */}
                <div className="absolute top-3 left-3 flex items-center gap-1.5">
                  <Badge variant={badge.variant} className="gap-1 px-2.5 py-1 text-xs backdrop-blur-md">
                    <Sparkles className="h-3 w-3" />
                    <span>
                      {Math.round(clip.score)} {badge.tier}
                    </span>
                  </Badge>
                </div>

                <div className="absolute bottom-3 right-3">
                  <span className="rounded-md bg-black/75 px-2 py-0.5 font-mono text-xs font-semibold text-white backdrop-blur-sm">
                    {durationStr}
                  </span>
                </div>

                {/* Hover Play Glyph */}
                <div className="absolute inset-0 flex items-center justify-center bg-black/25 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
                  <div className="flex h-14 w-14 items-center justify-center rounded-full bg-white/90 text-black shadow-lg">
                    <Play className="h-6 w-6 fill-black ml-1" />
                  </div>
                </div>
              </div>

              {/* Clip Details */}
              <div className="mt-3.5 flex flex-col gap-1.5 px-1">
                <h3 className="line-clamp-2 text-sm font-semibold text-foreground group-hover:text-primary transition-colors">
                  {clip.title}
                </h3>
                <p className="line-clamp-2 text-xs text-muted-foreground italic">
                  "{clip.hook}"
                </p>

                <div className="mt-1 flex flex-wrap gap-1">
                  {(clip.hashtags ?? []).slice(0, 3).map((tag) => (
                    <span
                      key={tag}
                      className="text-[11px] font-medium text-muted-foreground"
                    >
                      {tag}
                    </span>
                  ))}
                </div>

                <div className="mt-2 flex items-center justify-between border-t border-border/60 pt-2 text-xs text-muted-foreground">
                  <span className="font-medium text-foreground/80">
                    {activeStyle?.name ?? "Clean Pop"} · Edit
                  </span>
                  <span className="text-[11px]">Click to inspect</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Clip Detail & Editor Sheet */}
      <Sheet
        open={selectedClip !== null}
        onOpenChange={(open) => !open && setSelectedClip(null)}
        title={selectedClip?.title || "Clip Detail"}
        description={`Score ${Math.round(selectedClip?.score || 0)} · ${formatDuration(trimStart, trimEnd)}`}
        className="max-w-xl"
      >
        {selectedClip && (
          <div className="mt-4 flex flex-col gap-6 pb-6">
            {/* Video Player: final render by default, low-res preview on toggle */}
            <div className="relative mx-auto aspect-[9/16] w-full max-w-[280px] overflow-hidden rounded-xl border border-border bg-black shadow-lg">
              {selectedClip.output_file ? (
                <video
                  ref={videoRef}
                  key={`${selectedClip.id}-${usePreview ? "preview" : "video"}-${cacheBust(selectedClip.id)}`}
                  src={
                    usePreview
                      ? `${defaultApiClient.getPreviewUrl(job.id, selectedClip.id)}${cacheBust(selectedClip.id)}`
                      : `${defaultApiClient.getVideoUrl(job.id, selectedClip.id)}${cacheBust(selectedClip.id)}`
                  }
                  className="h-full w-full object-cover"
                  controls
                  playsInline
                  loop
                  aria-label={`Preview of ${selectedClip.title}`}
                />
              ) : (
                <img
                  src={`${defaultApiClient.getThumbnailUrl(job.id, selectedClip.id)}${cacheBust(selectedClip.id)}`}
                  alt={selectedClip.title}
                  className="h-full w-full object-cover"
                />
              )}
            </div>

            {/* Preview / final video toggle */}
            {selectedClip.output_file && (
              <div className="flex items-center justify-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setUsePreview((v) => !v)}
                  className="h-7 gap-1.5 text-xs text-muted-foreground"
                >
                  {usePreview ? (
                    <><Eye className="h-3.5 w-3.5" /> Preview proxy · switch to final</>
                  ) : (
                    <><EyeOff className="h-3.5 w-3.5" /> Final render · switch to preview</>
                  )}
                </Button>
              </div>
            )}

            {/* Hook & Rationale */}
            <div className="rounded-xl border border-border bg-card/60 p-3.5 flex flex-col gap-1.5 text-xs">
              <span className="font-semibold text-foreground">Hook:</span>
              <p className="text-muted-foreground italic">"{selectedClip.hook}"</p>
              <span className="font-semibold text-foreground mt-1">Why this was selected:</span>
              <p className="text-muted-foreground">{selectedClip.rationale}</p>
            </div>

            {/* Trim controls */}
            <div className="flex flex-col gap-3 rounded-xl border border-border bg-card/60 p-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  <Scissors className="h-3.5 w-3.5" />
                  <span>Trim & Pacing</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                  <span
                    className={`font-mono text-xs font-semibold ${
                      isShortWarning || isLongWarning ? "text-warning font-bold" : "text-foreground"
                    }`}
                  >
                    {currentClipDuration}s{" "}
                    {isShortWarning && `(Under ${minClip}s minimum)`}
                    {isLongWarning && `(Over ${maxClip}s maximum)`}
                  </span>
                </div>
              </div>

              {/* Start Trim */}
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="text-muted-foreground w-16">Start:</span>
                <span className="font-mono text-foreground">{trimStart.toFixed(1)}s</span>
                <div className="flex items-center gap-1">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={isReRendering}
                    onClick={(e) => handleNudgeStart(e.shiftKey ? -0.5 : -0.1)}
                    className="h-7 px-2 text-xs"
                    title="Nudge left (Shift for 0.5s)"
                  >
                    -0.1s
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={isReRendering}
                    onClick={(e) => handleNudgeStart(e.shiftKey ? 0.5 : 0.1)}
                    className="h-7 px-2 text-xs"
                    title="Nudge right (Shift for 0.5s)"
                  >
                    +0.1s
                  </Button>
                </div>
              </div>

              {/* End Trim */}
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="text-muted-foreground w-16">End:</span>
                <span className="font-mono text-foreground">{trimEnd.toFixed(1)}s</span>
                <div className="flex items-center gap-1">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={isReRendering}
                    onClick={(e) => handleNudgeEnd(e.shiftKey ? -0.5 : -0.1)}
                    className="h-7 px-2 text-xs"
                    title="Nudge left (Shift for 0.5s)"
                  >
                    -0.1s
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={isReRendering}
                    onClick={(e) => handleNudgeEnd(e.shiftKey ? 0.5 : 0.1)}
                    className="h-7 px-2 text-xs"
                    title="Nudge right (Shift for 0.5s)"
                  >
                    +0.1s
                  </Button>
                </div>
              </div>
            </div>

            {/* Caption Style Switcher */}
            <div className="flex flex-col gap-2">
              <label
                htmlFor="caption-style-select"
                className="text-xs font-semibold uppercase tracking-wider text-muted-foreground"
              >
                Caption Style
              </label>
              <select
                id="caption-style-select"
                value={selectedStyleId}
                onChange={(e) => setSelectedStyleId(e.target.value)}
                disabled={isReRendering}
                className="h-10 rounded-lg border border-input bg-card px-3 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer disabled:opacity-50"
              >
                {captionStyles.map((style) => (
                  <option key={style.id} value={style.id}>
                    {style.name} — {style.font_family}
                  </option>
                ))}
              </select>
            </div>

            {/* Re-render action */}
            <div className="flex flex-col gap-2">
              <Button
                onClick={() => { void handleReRender(); }}
                disabled={isReRendering}
                className="h-11 w-full font-semibold gap-2"
              >
                <RotateCcw className={`h-4 w-4 ${isReRendering ? "animate-spin" : ""}`} />
                {isReRendering
                  ? "Re-rendering in background…"
                  : "Re-render clip · ≈ 15 s"}
              </Button>
              {isReRendering && (
                <p className="text-center text-xs text-muted-foreground" role="status">
                  Re-rendering captions and video — this clip will refresh automatically.
                </p>
              )}
            </div>

            {/* Actions: Download MP4 & Copy Buttons */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 pt-2 border-t border-border">
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleDownloadMp4(selectedClip)}
                disabled={!selectedClip.output_file || isReRendering}
                className="text-xs font-medium gap-1.5"
              >
                <Download className="h-3.5 w-3.5" />
                Download MP4
              </Button>

              <Button
                variant="outline"
                size="sm"
                onClick={() => copyToClipboard(selectedClip.title, "Title copied")}
                className="text-xs font-medium gap-1.5"
              >
                <Copy className="h-3.5 w-3.5" />
                Copy title
              </Button>

              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  copyToClipboard((selectedClip.hashtags ?? []).join(" "), "Hashtags copied")
                }
                className="text-xs font-medium gap-1.5"
              >
                <Check className="h-3.5 w-3.5" />
                Copy hashtags
              </Button>
            </div>
          </div>
        )}
      </Sheet>
    </div>
  );
}
