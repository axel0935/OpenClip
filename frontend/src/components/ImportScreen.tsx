import { useState, useRef } from "react";
import {
  Link as LinkIcon,
  UploadCloud,
  ChevronDown,
  ChevronUp,
  FileVideo,
  X,
  AlertTriangle,
  HelpCircle,
  Plus,
  Minus,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { CaptionStylePicker } from "@/components/CaptionStylePicker";
import { useToast } from "@/components/ui/use-toast";
import type { CaptionStyle, JobCreate, JobOptions } from "@/api/client";

interface ImportScreenProps {
  captionStyles: CaptionStyle[];
  onSubmit: (jobCreate: JobCreate) => void;
  /** Accepts an optional progress callback (0–100) for XHR upload progress. */
  onUploadFile: (
    file: File,
    onProgress?: (pct: number) => void,
  ) => Promise<{ upload_id: string; filename: string; size_bytes: number }>;
  isSubmitting?: boolean;
}

const SUPPORTED_EXTENSIONS = [".mp4", ".mov", ".mkv", ".webm", ".m4v"];

export function ImportScreen({
  captionStyles,
  onSubmit,
  onUploadFile,
  isSubmitting = false,
}: ImportScreenProps) {
  const { toast } = useToast();

  // Source state
  const [url, setUrl] = useState("");
  const [urlError, setUrlError] = useState<string | null>(null);
  const [uploadedFile, setUploadedFile] = useState<{
    id: string;
    name: string;
    sizeFormatted: string;
  } | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [dropError, setDropError] = useState<string | null>(null);

  // Options state
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [minSeconds, setMinSeconds] = useState(20);
  const [maxSeconds, setMaxSeconds] = useState(90);
  const [maxClips, setMaxClips] = useState(8);
  const [layout, setLayout] = useState<"auto" | "fill" | "fit">("auto");
  const [selectedStyleId, setSelectedStyleId] = useState("clean-pop");

  const [showCpuNotice, setShowCpuNotice] = useState(false);
  const [showCpuHelp, setShowCpuHelp] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  };

  const validateUrl = (value: string): boolean => {
    if (!value.trim()) return false;
    try {
      const parsed = new URL(value.trim());
      if (parsed.protocol === "http:" || parsed.protocol === "https:") {
        setUrlError(null);
        return true;
      }
    } catch {
      // not a valid URL
    }
    setUrlError("That doesn't look like a link. Use an http(s) URL or drop a file.");
    return false;
  };

  const handleUrlChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setUrl(val);
    if (urlError) setUrlError(null);
    if (val.toLowerCase().includes("podcast") || val.toLowerCase().includes("interview")) {
      setShowCpuNotice(true);
    } else {
      setShowCpuNotice(false);
    }
  };

  const handleUrlBlur = () => {
    if (url.trim()) {
      validateUrl(url);
    }
  };

  const handleFileProcess = async (file: File) => {
    setDropError(null);
    const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!SUPPORTED_EXTENSIONS.includes(ext)) {
      setDropError(
        "OpenClip reads MP4, MOV, MKV, WebM and M4V. Convert this file first.",
      );
      return;
    }

    if (file.size > 4 * 1024 * 1024 * 1024) {
      setDropError("File is larger than the 4 GB limit.");
      return;
    }

    // Show real upload progress (the callback comes from XHR in http mode)
    setUploadProgress(5);

    try {
      const resp = await onUploadFile(file, (pct) => {
        setUploadProgress(Math.max(5, pct));
      });
      setUploadProgress(100);
      setTimeout(() => {
        setUploadProgress(null);
        setUploadedFile({
          id: resp.upload_id,
          name: resp.filename,
          sizeFormatted: formatFileSize(resp.size_bytes),
        });
        setUrl("");
        if (resp.size_bytes > 500 * 1024 * 1024) {
          setShowCpuNotice(true);
        }
      }, 200);
    } catch {
      setUploadProgress(null);
      setDropError("Failed to copy video file.");
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const file = e.dataTransfer.files[0];
      handleFileProcess(file);
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const file = e.target.files[0];
      handleFileProcess(file);
    }
  };

  // Min / max clamp and swap
  const handleMinChange = (val: number) => {
    if (val > maxSeconds) {
      setMinSeconds(maxSeconds);
      setMaxSeconds(val);
      toast("Swapped min and max duration", "warning");
    } else {
      setMinSeconds(Math.max(10, Math.min(val, 180)));
    }
  };

  const handleMaxChange = (val: number) => {
    if (val < minSeconds) {
      setMaxSeconds(minSeconds);
      setMinSeconds(val);
      toast("Swapped min and max duration", "warning");
    } else {
      setMaxSeconds(Math.max(20, Math.min(val, 300)));
    }
  };

  const hasValidSource = (url.trim() !== "" && !urlError) || uploadedFile !== null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!hasValidSource) return;

    if (url.trim() && !validateUrl(url)) {
      return;
    }

    const options: JobOptions = {
      min_clip_seconds: minSeconds,
      max_clip_seconds: maxSeconds,
      max_clips: maxClips,
      layout,
      caption_style_id: selectedStyleId,
      whisper_profile: "auto",
      analysis_mode: "auto",
    };

    if (uploadedFile) {
      onSubmit({
        source: {
          type: "upload",
          value: uploadedFile.id,
        },
        options,
      });
    } else {
      onSubmit({
        source: {
          type: "url",
          value: url.trim(),
        },
        options,
      });
    }
  };

  // Find style name for options summary
  const selectedStyle = captionStyles.find((s) => s.id === selectedStyleId);
  const layoutLabel =
    layout === "auto" ? "Auto" : layout === "fill" ? "Follow face" : "Fit blurred";

  const optionsSummary = `${minSeconds}–${maxSeconds} s · ${maxClips} clips · ${layoutLabel} · ${selectedStyle?.name || "Clean Pop"}`;

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col px-4 py-8 md:py-16">
      {/* Hero */}
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-extrabold tracking-tight text-foreground md:text-4xl">
          Paste a link, or drop a file.
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Turn long videos into vertical highlights with dynamic captions. Runs completely on this machine.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-5">
        {/* URL Input */}
        <div className="flex flex-col gap-1.5">
          <label htmlFor="video-url" className="sr-only">
            Video URL
          </label>
          <div className="relative flex items-center">
            <LinkIcon className="pointer-events-none absolute left-3.5 h-4 w-4 text-muted-foreground" />
            <input
              id="video-url"
              type="url"
              value={url}
              onChange={handleUrlChange}
              onBlur={handleUrlBlur}
              placeholder="https://youtube.com/watch?v=…"
              disabled={uploadedFile !== null || isSubmitting}
              className="h-12 w-full rounded-xl border border-input bg-card pl-10 pr-24 text-sm text-foreground placeholder:text-muted-foreground/60 focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!url.trim() || uploadedFile !== null || isSubmitting}
              onClick={() => validateUrl(url)}
              className="absolute right-2 h-8 text-xs font-semibold px-3"
            >
              Fetch
            </Button>
          </div>
          {urlError && (
            <p className="text-xs text-destructive mt-1 font-medium" role="alert">
              {urlError}
            </p>
          )}
        </div>

        {/* Drop zone / File chip */}
        {uploadedFile ? (
          <div className="flex items-center justify-between rounded-xl border border-primary/40 bg-card p-3.5">
            <div className="flex items-center gap-3 overflow-hidden">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <FileVideo className="h-5 w-5" />
              </div>
              <div className="overflow-hidden text-left">
                <p className="truncate text-sm font-semibold text-foreground">
                  {uploadedFile.name}
                </p>
                <p className="text-xs text-muted-foreground">
                  {uploadedFile.sizeFormatted} · Ready on this machine
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => {
                setUploadedFile(null);
                setShowCpuNotice(false);
              }}
              className="rounded-lg p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label="Remove video"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        ) : (
          <div
            onDragOver={(e) => e.preventDefault()}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className="flex cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed border-border bg-card/40 px-6 py-7 text-center transition-colors hover:border-foreground/30 hover:bg-card/70"
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                fileInputRef.current?.click();
              }
            }}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".mp4,.mov,.mkv,.webm,.m4v"
              onChange={handleFileSelect}
              className="hidden"
            />
            <UploadCloud className="h-8 w-8 text-muted-foreground/60 mb-2" />
            <p className="text-sm font-medium text-foreground">
              Drop a video here · MP4, MOV, MKV, WebM, M4V
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              Up to 4 GB · Video never leaves your computer
            </p>

            {uploadProgress !== null && (
              <div className="mt-3 w-full max-w-xs">
                <Progress value={uploadProgress} className="h-1.5" />
                <p className="mt-1 text-[11px] text-muted-foreground">
                  Copying locally… {uploadProgress}%
                </p>
              </div>
            )}

            {dropError && (
              <p className="mt-2 text-xs text-destructive font-medium" role="alert">
                {dropError}
              </p>
            )}
          </div>
        )}

        {/* Collapsible Options Panel */}
        <div className="rounded-xl border border-border bg-card/70 overflow-hidden transition-all">
          <button
            type="button"
            onClick={() => setOptionsOpen(!optionsOpen)}
            className="flex w-full items-center justify-between p-4 text-left text-sm font-medium hover:bg-muted/30 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-expanded={optionsOpen}
          >
            <div className="flex items-center gap-2">
              <span className="font-semibold text-foreground">Options</span>
              <span className="text-xs text-muted-foreground">· {optionsSummary}</span>
            </div>
            {optionsOpen ? (
              <ChevronUp className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            )}
          </button>

          {optionsOpen && (
            <div className="border-t border-border p-5 flex flex-col gap-6">
              {/* Clip length */}
              <div className="flex flex-col gap-3">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Clip length (seconds)
                  </label>
                  <span className="text-xs font-mono text-foreground">
                    {minSeconds}s – {maxSeconds}s
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="flex flex-col gap-1">
                    <span className="text-[11px] text-muted-foreground">Min duration</span>
                    <input
                      type="number"
                      min={10}
                      max={180}
                      value={minSeconds}
                      onChange={(e) => handleMinChange(Number(e.target.value))}
                      className="h-9 w-full rounded-lg border border-input bg-card px-3 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <span className="text-[11px] text-muted-foreground">Max duration</span>
                    <input
                      type="number"
                      min={20}
                      max={300}
                      value={maxSeconds}
                      onChange={(e) => handleMaxChange(Number(e.target.value))}
                      className="h-9 w-full rounded-lg border border-input bg-card px-3 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                    />
                  </div>
                </div>
              </div>

              {/* Number of clips */}
              <div className="flex items-center justify-between">
                <div>
                  <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Max clips
                  </label>
                  <p className="text-xs text-muted-foreground">Target count of top moments</p>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setMaxClips((c) => Math.max(1, c - 1))}
                    disabled={maxClips <= 1}
                    className="h-8 w-8 p-0"
                    aria-label="Decrease clips"
                  >
                    <Minus className="h-3 w-3" />
                  </Button>
                  <span className="w-6 text-center font-mono text-sm font-semibold text-foreground">
                    {maxClips}
                  </span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setMaxClips((c) => Math.min(8, c + 1))}
                    disabled={maxClips >= 8}
                    className="h-8 w-8 p-0"
                    aria-label="Increase clips"
                  >
                    <Plus className="h-3 w-3" />
                  </Button>
                </div>
              </div>

              {/* Reframing */}
              <div className="flex flex-col gap-2">
                <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Reframing layout
                </label>
                <div className="grid grid-cols-3 gap-2">
                  <button
                    type="button"
                    onClick={() => setLayout("auto")}
                    className={`rounded-lg border p-2.5 text-xs font-medium transition-colors ${
                      layout === "auto"
                        ? "border-primary bg-primary text-primary-foreground font-semibold"
                        : "border-border bg-card text-muted-foreground hover:bg-muted/50"
                    }`}
                  >
                    Auto
                  </button>
                  <button
                    type="button"
                    onClick={() => setLayout("fill")}
                    className={`rounded-lg border p-2.5 text-xs font-medium transition-colors ${
                      layout === "fill"
                        ? "border-primary bg-primary text-primary-foreground font-semibold"
                        : "border-border bg-card text-muted-foreground hover:bg-muted/50"
                    }`}
                  >
                    Follow face
                  </button>
                  <button
                    type="button"
                    onClick={() => setLayout("fit")}
                    className={`rounded-lg border p-2.5 text-xs font-medium transition-colors ${
                      layout === "fit"
                        ? "border-primary bg-primary text-primary-foreground font-semibold"
                        : "border-border bg-card text-muted-foreground hover:bg-muted/50"
                    }`}
                  >
                    Fit blurred
                  </button>
                </div>
              </div>

              {/* Caption style picker */}
              <div className="flex flex-col gap-2">
                <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Caption style
                </label>
                <CaptionStylePicker
                  styles={captionStyles}
                  selectedId={selectedStyleId}
                  onSelect={setSelectedStyleId}
                  compact={true}
                />
              </div>
            </div>
          )}
        </div>

        {/* CPU Notice for long video */}
        {showCpuNotice && (
          <div className="flex items-start gap-3 rounded-xl border border-warning/40 bg-warning/10 p-3.5 text-xs text-warning">
            <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="font-semibold text-foreground">Long video notice</p>
              <p className="text-muted-foreground mt-0.5">
                This video is over 45 min. Expect roughly 35–55 min on this CPU.
              </p>
              {showCpuHelp && (
                <p className="mt-2 text-muted-foreground border-t border-warning/20 pt-2">
                  OpenClip transcribes audio and scans faces locally on CPU using optimized INT8 inference. Processing speed depends on your machine cores.
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={() => setShowCpuHelp(!showCpuHelp)}
              className="text-muted-foreground hover:text-foreground text-xs flex items-center gap-1"
            >
              <HelpCircle className="h-3.5 w-3.5" />
              <span>Why so long?</span>
            </button>
          </div>
        )}

        {/* Submit button area */}
        <div className="mt-2 flex flex-col items-center gap-2">
          {!hasValidSource && (
            <p className="text-xs text-muted-foreground">
              Paste a link or select a video file first
            </p>
          )}
          <Button
            type="submit"
            disabled={!hasValidSource || isSubmitting}
            className="h-12 w-full text-base font-semibold shadow-md rounded-xl"
          >
            {isSubmitting ? "Starting…" : "Start clipping"}
          </Button>
        </div>
      </form>
    </div>
  );
}
