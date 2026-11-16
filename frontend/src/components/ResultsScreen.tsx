import { useState, useMemo } from "react";
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
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import { Progress } from "@/components/ui/progress";
import { useToast } from "@/components/ui/use-toast";
import type { CaptionStyle, Clip, ClipUpdate, Job } from "@/api/client";
import { CaptionStylePreview } from "./CaptionStylePicker";

interface ResultsScreenProps {
  job: Job;
  captionStyles: CaptionStyle[];
  onNewVideo: () => void;
  onUpdateClip: (clipId: string, update: ClipUpdate) => Promise<Clip>;
}

type SortOption = "score_desc" | "duration_desc" | "duration_asc" | "source_order";

export function ResultsScreen({
  job,
  captionStyles,
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
  const [isReRendering, setIsReRendering] = useState(false);
  const [reRenderProgress, setReRenderProgress] = useState(0);

  // Download all state
  const [isDownloadingZip, setIsDownloadingZip] = useState(false);
  const [downloadZipProgress, setDownloadZipProgress] = useState(0);

  const clips = useMemo(() => job.clips || [], [job.clips]);

  const handleSelectClip = (clip: Clip) => {
    setSelectedClip(clip);
    setTrimStart(clip.start);
    setTrimEnd(clip.end);
    setIsReRendering(false);
    setReRenderProgress(0);
  };

  // Sorting
  const sortedClips = useMemo(() => {
    const list = [...clips];
    switch (sortOption) {
      case "score_desc":
        return list.sort((a, b) => b.score - a.score);
      case "duration_desc":
        return list.sort((a, b) => (b.end - b.start) - (a.end - a.start));
      case "duration_asc":
        return list.sort((a, b) => (a.end - a.start) - (b.end - b.start));
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
    if (score >= 70) {
      return {
        variant: "scoreHigh" as const,
        tier: "Strong",
      };
    }
    if (score >= 40) {
      return {
        variant: "scoreMedium" as const,
        tier: "Good",
      };
    }
    return {
      variant: "scoreLow" as const,
      tier: "Fair",
    };
  };

  const handleNudgeStart = (delta: number) => {
    setTrimStart((s) => {
      const next = Math.max(0, Math.round((s + delta) * 10) / 10);
      if (next >= trimEnd - 5) return s;
      return next;
    });
  };

  const handleNudgeEnd = (delta: number) => {
    setTrimEnd((e) => {
      const maxLimit = (job.media?.duration || 3600);
      const next = Math.min(maxLimit, Math.round((e + delta) * 10) / 10);
      if (next <= trimStart + 5) return e;
      return next;
    });
  };

  const currentClipDuration = Math.round((trimEnd - trimStart) * 10) / 10;
  const isShortWarning = currentClipDuration < 20;

  const handleReRender = async () => {
    if (!selectedClip) return;
    setIsReRendering(true);
    setReRenderProgress(10);

    const interval = setInterval(() => {
      setReRenderProgress((p) => {
        if (p >= 90) {
          clearInterval(interval);
          return 90;
        }
        return p + 25;
      });
    }, 200);

    try {
      const updated = await onUpdateClip(selectedClip.id, {
        start: trimStart,
        end: trimEnd,
        caption_style_id: selectedStyleId,
      });
      clearInterval(interval);
      setReRenderProgress(100);
      setTimeout(() => {
        setIsReRendering(false);
        setSelectedClip(updated);
        toast("Clip re-rendered successfully");
      }, 300);
    } catch {
      clearInterval(interval);
      setIsReRendering(false);
      toast("Failed to re-render clip", "destructive");
    }
  };

  const handleDownloadZip = () => {
    setIsDownloadingZip(true);
    setDownloadZipProgress(15);
    const interval = setInterval(() => {
      setDownloadZipProgress((p) => {
        if (p >= 90) {
          clearInterval(interval);
          return 90;
        }
        return p + 25;
      });
    }, 250);

    setTimeout(() => {
      clearInterval(interval);
      setDownloadZipProgress(100);
      setTimeout(() => {
        setIsDownloadingZip(false);
        toast(`${clips.length} clips · 142 MB packaged (inert in mock mode)`);
      }, 400);
    }, 1500);
  };

  const copyToClipboard = (text: string, message: string) => {
    navigator.clipboard.writeText(text);
    toast(message);
  };

  const activeStyle = captionStyles.find((s) => s.id === selectedStyleId) || captionStyles[0];

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col px-4 py-6 md:px-8">
      {/* Top Header bar */}
      <div className="flex flex-col gap-4 border-b border-border pb-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="truncate text-xl font-bold text-foreground max-w-lg md:text-2xl">
            {job.media?.title || job.source.value}
          </h1>
          <p className="text-xs text-muted-foreground mt-1">
            {clips.length} clips · {totalDurationSaved} min saved
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
            {isDownloadingZip
              ? `Packaging ${downloadZipProgress}%…`
              : "Download all (.zip)"}
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
                  src={clip.thumbnail || ""}
                  alt={clip.title}
                  className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
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
                    {activeStyle.name} · Edit
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
            {/* Player Preview */}
            <div className="relative mx-auto aspect-[9/16] w-full max-w-[280px] overflow-hidden rounded-xl border border-border bg-black shadow-lg">
              <img
                src={selectedClip.thumbnail || ""}
                alt={selectedClip.title}
                className="h-full w-full object-cover"
              />
              {/* Live Caption Preview Overlay */}
              <div className="absolute bottom-6 left-2 right-2 flex justify-center">
                <CaptionStylePreview style={activeStyle} isHovered={true} />
              </div>
            </div>

            <p className="text-center text-xs text-muted-foreground italic">
              Preview only — captions are burned in on re-render.
            </p>

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
                      isShortWarning ? "text-warning font-bold" : "text-foreground"
                    }`}
                  >
                    {currentClipDuration}s {isShortWarning && "(Under 20s minimum)"}
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
              <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Caption Style Switcher
              </label>
              <select
                value={selectedStyleId}
                onChange={(e) => setSelectedStyleId(e.target.value)}
                disabled={isReRendering}
                className="h-10 rounded-lg border border-input bg-card px-3 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer"
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
                onClick={handleReRender}
                disabled={isReRendering}
                className="h-11 w-full font-semibold gap-2"
              >
                <RotateCcw className={`h-4 w-4 ${isReRendering ? "animate-spin" : ""}`} />
                {isReRendering
                  ? `Rendering… ${reRenderProgress}%`
                  : "Re-render clip · ≈ 15 s"}
              </Button>
              {isReRendering && <Progress value={reRenderProgress} className="h-1.5" />}
            </div>

            {/* Actions: Download MP4 & Copy Buttons */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 pt-2 border-t border-border">
              <Button
                variant="outline"
                size="sm"
                onClick={() => toast("Downloaded MP4 (inert in mock mode)")}
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
