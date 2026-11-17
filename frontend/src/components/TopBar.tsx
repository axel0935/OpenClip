import { useState, useEffect } from "react";
import { History, Settings, Sun, Moon, Monitor, Video, CheckCircle2, Clock, AlertCircle, Trash2, WifiOff } from "lucide-react";
import { Sheet } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import type { Job } from "@/api/client";

interface TopBarProps {
  jobs: Job[];
  currentJobId?: string | null;
  onSelectJob: (jobId: string) => void;
  onNewVideo: () => void;
  /** When true the history drawer shows a "backend unreachable" notice. */
  backendUnreachable?: boolean;
  onDeleteJob?: (jobId: string) => void;
}

type Theme = "dark" | "light" | "system";

export function TopBar({ jobs, currentJobId, onSelectJob, onNewVideo, backendUnreachable = false, onDeleteJob }: TopBarProps) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      if (typeof window !== "undefined" && window.localStorage) {
        return (window.localStorage.getItem("openclip-theme") as Theme) || "dark";
      }
    } catch {
      // fallback
    }
    return "dark";
  });

  useEffect(() => {
    const root = document.documentElement;
    try {
      if (typeof window !== "undefined" && window.localStorage) {
        window.localStorage.setItem("openclip-theme", theme);
      }
    } catch {
      // ignore
    }

    if (theme === "light") {
      root.classList.add("light");
    } else if (theme === "dark") {
      root.classList.remove("light");
    } else {
      // system
      const prefersDark =
        typeof window !== "undefined" && window.matchMedia
          ? window.matchMedia("(prefers-color-scheme: dark)").matches
          : true;
      if (prefersDark) {
        root.classList.remove("light");
      } else {
        root.classList.add("light");
      }
    }
  }, [theme]);

  return (
    <>
      <header className="sticky top-0 z-40 flex h-14 w-full items-center justify-between border-b border-border bg-background/80 px-4 md:px-6 backdrop-blur-md">
        {/* Brand wordmark */}
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onNewVideo}
            className="flex items-center gap-2 font-semibold tracking-tight text-foreground hover:opacity-90 transition-opacity focus:outline-none focus:ring-2 focus:ring-ring rounded-md px-1 py-0.5"
          >
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary text-primary-foreground font-black text-sm">
              O
            </div>
            <span className="text-lg font-bold">OpenClip</span>
          </button>
        </div>

        {/* Action icons */}
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setHistoryOpen(true)}
            className="flex items-center gap-1.5 text-xs font-medium border-border hover:bg-muted text-foreground"
            aria-label="History"
          >
            <History className="h-4 w-4" />
            <span className="hidden sm:inline">History</span>
            {jobs.length > 0 && (
              <Badge variant="secondary" className="ml-0.5 px-1.5 py-0 text-[10px]">
                {jobs.length}
              </Badge>
            )}
          </Button>

          <Button
            variant="outline"
            size="sm"
            onClick={() => setSettingsOpen(true)}
            className="flex items-center gap-1.5 text-xs font-medium border-border hover:bg-muted text-foreground"
            aria-label="Settings"
          >
            <Settings className="h-4 w-4" />
            <span className="hidden sm:inline">Settings</span>
          </Button>
        </div>
      </header>

      {/* History Drawer */}
      <Sheet
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        title="Session History"
        description="Videos processed on this machine during this session."
      >
        <div className="mt-4 flex flex-col gap-3">
          {/* Backend unreachable notice */}
          {backendUnreachable && (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive"
            >
              <WifiOff className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold">Cannot reach OpenClip.</p>
                <p className="text-muted-foreground mt-0.5">
                  Is <code>openclip serve</code> running? Run{" "}
                  <code>openclip doctor</code> to diagnose.
                </p>
              </div>
            </div>
          )}

          {jobs.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <Video className="h-10 w-10 text-muted-foreground/40 stroke-1 mb-3" />
              <p className="text-sm text-muted-foreground">No jobs yet. Import a video to start.</p>
              <Button
                variant="outline"
                size="sm"
                className="mt-4"
                onClick={() => {
                  setHistoryOpen(false);
                  onNewVideo();
                }}
              >
                + New video
              </Button>
            </div>
          ) : (
            <>
              <div className="flex justify-between items-center pb-2">
                <span className="text-xs text-muted-foreground">{jobs.length} jobs</span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setHistoryOpen(false);
                    onNewVideo();
                  }}
                  className="text-xs h-7"
                >
                  + New video
                </Button>
              </div>
              <div className="flex flex-col gap-2">
                {jobs.map((job) => {
                  const isCurrent = job.id === currentJobId;
                  const title = job.media?.title || job.source.value;
                  const dateLabel = new Date(job.created_at).toLocaleString([], {
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  });

                  return (
                    <div key={job.id} className="relative group">
                      <button
                        type="button"
                        onClick={() => {
                          setHistoryOpen(false);
                          onSelectJob(job.id);
                        }}
                        className={`flex w-full flex-col rounded-lg border p-3 text-left transition-colors ${
                          isCurrent
                            ? "border-primary bg-muted/60"
                            : "border-border bg-card/60 hover:bg-muted/40 hover:border-foreground/20"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2 pr-6">
                          <span className="truncate text-sm font-medium text-foreground max-w-[200px]">
                            {title}
                          </span>
                          <div className="flex items-center gap-1">
                            {job.status === "completed" && (
                              <Badge variant="success" className="text-[10px] gap-1 px-1.5 py-0">
                                <CheckCircle2 className="h-3 w-3" />
                                <span>{job.clips?.length ?? 0} clips</span>
                              </Badge>
                            )}
                            {job.status === "running" && (
                              <Badge variant="warning" className="text-[10px] gap-1 px-1.5 py-0">
                                <Clock className="h-3 w-3 animate-spin" />
                                <span>{Math.round(job.progress * 100)}%</span>
                              </Badge>
                            )}
                            {job.status === "failed" && (
                              <Badge variant="destructive" className="text-[10px] gap-1 px-1.5 py-0">
                                <AlertCircle className="h-3 w-3" />
                                <span>Failed</span>
                              </Badge>
                            )}
                            {job.status === "cancelled" && (
                              <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                                Cancelled
                              </Badge>
                            )}
                          </div>
                        </div>
                        <div className="mt-1 flex items-center justify-between text-xs text-muted-foreground">
                          <span className="font-mono text-[11px]">{job.id.slice(0, 16)}…</span>
                          <span>{dateLabel}</span>
                        </div>
                      </button>
                      {/* Delete button (only for non-running jobs) */}
                      {onDeleteJob && job.status !== "running" && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onDeleteJob(job.id);
                          }}
                          className="absolute right-2 top-2.5 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100 focus:opacity-100"
                          aria-label={`Delete job ${job.id}`}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </Sheet>

      {/* Settings Drawer */}
      <Sheet
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        title="Settings"
        description="Preferences and machine configuration."
      >
        <div className="mt-6 flex flex-col gap-6">
          {/* Theme selector */}
          <div className="flex flex-col gap-3">
            <label className="text-sm font-semibold text-foreground">Theme</label>
            <div className="grid grid-cols-3 gap-2">
              <button
                type="button"
                onClick={() => setTheme("dark")}
                className={`flex flex-col items-center justify-center gap-2 rounded-lg border p-3 text-xs font-medium transition-colors ${
                  theme === "dark"
                    ? "border-primary bg-muted text-foreground ring-1 ring-primary"
                    : "border-border bg-card text-muted-foreground hover:bg-muted/50"
                }`}
              >
                <Moon className="h-4 w-4" />
                <span>Dark</span>
              </button>
              <button
                type="button"
                onClick={() => setTheme("light")}
                className={`flex flex-col items-center justify-center gap-2 rounded-lg border p-3 text-xs font-medium transition-colors ${
                  theme === "light"
                    ? "border-primary bg-muted text-foreground ring-1 ring-primary"
                    : "border-border bg-card text-muted-foreground hover:bg-muted/50"
                }`}
              >
                <Sun className="h-4 w-4" />
                <span>Light</span>
              </button>
              <button
                type="button"
                onClick={() => setTheme("system")}
                className={`flex flex-col items-center justify-center gap-2 rounded-lg border p-3 text-xs font-medium transition-colors ${
                  theme === "system"
                    ? "border-primary bg-muted text-foreground ring-1 ring-primary"
                    : "border-border bg-card text-muted-foreground hover:bg-muted/50"
                }`}
              >
                <Monitor className="h-4 w-4" />
                <span>System</span>
              </button>
            </div>
          </div>

          <Separator />

          {/* Local promise notice */}
          <div className="rounded-lg border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
            <p className="font-semibold text-foreground mb-1">Local and private</p>
            <p>
              OpenClip processes videos completely on this machine. No account, no cloud telemetry, and no data leaves your computer.
            </p>
          </div>

          <Separator />

          <div className="flex flex-col gap-1 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">OpenClip v0.1.0</span>
            <span>Apache-2.0 License</span>
            <a
              href="https://github.com/axel0935/OpenClip"
              target="_blank"
              rel="noreferrer"
              className="text-foreground underline underline-offset-4 hover:opacity-80 mt-1"
            >
              GitHub repository
            </a>
          </div>
        </div>
      </Sheet>
    </>
  );
}
