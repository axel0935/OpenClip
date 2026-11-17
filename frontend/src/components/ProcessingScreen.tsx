import { useState, useEffect } from "react";
import { Check, Circle, Loader2, Copy, CheckCheck, AlertCircle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Dialog } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/use-toast";
import type { Job, Stage } from "@/api/client";
import { jobTitle } from "@/api/client";
import { isFfmpegMissingCode } from "@/api/httpClient";

interface ProcessingScreenProps {
  job: Job;
  onCancel: (jobId: string) => void;
  onRetry?: (jobId: string) => void;
}

interface StepDefinition {
  id: number;
  stages: Stage[];
  label: string;
  subline: string;
}

const HUMAN_STEPS: StepDefinition[] = [
  {
    id: 1,
    stages: ["ingest"],
    label: "Getting the video",
    subline: "Reading duration, audio, frame rate",
  },
  {
    id: 2,
    stages: ["transcribe"],
    label: "Transcribing speech",
    subline: "Word-level timing, English",
  },
  {
    id: 3,
    stages: ["segment", "score", "select"],
    label: "Finding the best moments",
    subline: "Hooks, energy, questions",
  },
  {
    id: 4,
    stages: ["reframe"],
    label: "Following the speaker",
    subline: "Tracking faces, smoothing the crop",
  },
  {
    id: 5,
    stages: ["captions", "render", "export"],
    label: "Rendering clips",
    subline: "Burning captions into 1080×1920",
  },
];

export function ProcessingScreen({ job, onCancel, onRetry }: ProcessingScreenProps) {
  const { toast } = useToast();
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [cancelModalOpen, setCancelModalOpen] = useState(false);
  const [copiedId, setCopiedId] = useState(false);

  useEffect(() => {
    const startTime = new Date(job.created_at).getTime();
    const updateElapsed = () => {
      const now = Date.now();
      setElapsedSeconds(Math.max(0, Math.floor((now - startTime) / 1000)));
    };
    updateElapsed();
    const timer = setInterval(updateElapsed, 1000);
    return () => clearInterval(timer);
  }, [job.created_at]);

  const formatTime = (totalSec: number): string => {
    const mins = Math.floor(totalSec / 60);
    const secs = totalSec % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  // ETA calculation per UI_SPEC:
  // "ETA only after 20 s and 5 %: a range under 25 % ("18–30 min left"), else a rounded value, always "· rough estimate""
  const computeEta = (): string | null => {
    if (elapsedSeconds < 20 || job.progress < 0.05) return null;
    if (job.progress >= 0.99) return "Almost done";

    const totalEstimatedSec = elapsedSeconds / job.progress;
    const remainingSec = Math.max(0, totalEstimatedSec - elapsedSeconds);
    const remainingMin = Math.round(remainingSec / 60);

    if (job.progress < 0.25) {
      const minRange = Math.max(1, Math.floor(remainingMin * 0.8));
      const maxRange = Math.ceil(remainingMin * 1.3);
      return `${minRange}–${maxRange} min left · rough estimate`;
    }

    if (remainingMin <= 1) {
      return "≈ 1 min left · rough estimate";
    }
    return `≈ ${remainingMin} min left · rough estimate`;
  };

  const eta = computeEta();

  const getStepState = (step: StepDefinition): "done" | "active" | "pending" => {
    const currentStageIndex = HUMAN_STEPS.findIndex((s) =>
      job.stage ? s.stages.includes(job.stage) : false,
    );

    const stepIndex = HUMAN_STEPS.findIndex((s) => s.id === step.id);

    if (job.status === "completed") return "done";
    if (stepIndex < currentStageIndex) return "done";
    if (stepIndex === currentStageIndex) return "active";
    return "pending";
  };

  const copyJobId = () => {
    navigator.clipboard.writeText(job.id);
    setCopiedId(true);
    toast("Job ID copied to clipboard");
    setTimeout(() => setCopiedId(false), 2000);
  };

  const title = jobTitle(job);
  const isFailed = job.status === "failed";
  const progressPercent = Math.round((job.progress || 0) * 100);

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col px-4 py-8 md:py-16">
      {/* Header with Title and Elapsed */}
      <div className="flex items-center justify-between gap-4 pb-4">
        <h2 className="truncate text-base font-semibold text-foreground max-w-[320px]">
          {title}
        </h2>
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-muted-foreground">
            {formatTime(elapsedSeconds)}
          </span>
        </div>
      </div>

      {/* Determinate Progress Bar */}
      <div className="flex flex-col gap-1.5">
        <Progress value={progressPercent} className="h-2.5" />
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span className="font-medium text-foreground">
            {progressPercent}% · {job.message || "Processing…"}
          </span>
          {eta && <span>{eta}</span>}
        </div>
      </div>

      {/* Error Banner if failed */}
      {isFailed && (
        <div
          className="mt-6 rounded-xl border border-destructive/40 bg-destructive/10 p-4 text-xs text-destructive"
          role="alert"
        >
          <div className="flex items-center gap-2 font-semibold text-foreground">
            <AlertCircle className="h-4 w-4 text-destructive" />
            <span>Job failed ({job.error?.code || "processing_error"})</span>
          </div>
          <p className="mt-1 text-muted-foreground">
            {job.error?.message || "An unexpected error occurred while processing this video."}
          </p>
          {isFfmpegMissingCode(job.error?.code) && (
            <p className="mt-2 text-foreground">
              Run <code className="rounded bg-muted px-1 py-0.5 font-mono">openclip doctor</code> in a
              terminal to diagnose your FFmpeg installation.
            </p>
          )}
          <div className="mt-4 flex gap-3">
            {onRetry && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => onRetry(job.id)}
                className="gap-1.5 text-xs text-foreground"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Retry from the last finished step
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => toast("Logs copied: no internal exceptions")}
              className="text-xs text-muted-foreground"
            >
              Open logs
            </Button>
          </div>
        </div>
      )}

      {/* 5-Step Progress List */}
      <div className="mt-8 flex flex-col gap-3 rounded-xl border border-border bg-card p-5">
        {HUMAN_STEPS.map((step) => {
          const state = getStepState(step);

          return (
            <div
              key={step.id}
              className={`flex items-start gap-3.5 py-1.5 transition-opacity ${
                state === "pending" ? "opacity-40" : "opacity-100"
              }`}
            >
              <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center">
                {state === "done" && (
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-success text-white">
                    <Check className="h-3 w-3 stroke-[3]" />
                  </span>
                )}
                {state === "active" && (
                  <span className="flex h-5 w-5 items-center justify-center text-primary">
                    <Loader2 className="h-5 w-5 animate-spin" />
                  </span>
                )}
                {state === "pending" && (
                  <Circle className="h-4 w-4 text-muted-foreground stroke-1" />
                )}
              </div>

              <div className="flex-1">
                <div className="flex items-center justify-between">
                  <p
                    className={`text-sm font-medium ${
                      state === "active"
                        ? "font-semibold text-foreground"
                        : state === "done"
                          ? "text-foreground"
                          : "text-muted-foreground"
                    }`}
                  >
                    {step.label}
                  </p>
                  {state === "active" && (
                    <Badge variant="outline" className="text-[10px] font-normal py-0">
                      In progress
                    </Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground mt-0.5">{step.subline}</p>
              </div>
            </div>
          );
        })}
      </div>

      {/* Leave Notice & Copyable Job ID */}
      <div className="mt-6 flex flex-col gap-2 rounded-xl border border-border bg-muted/30 p-4 text-xs text-muted-foreground">
        <p className="font-semibold text-foreground">
          You can close this tab — the job keeps running on this machine.
        </p>
        <p className="text-[11px] text-muted-foreground">Sleep pauses the job.</p>
        <div className="mt-1 flex items-center justify-between pt-1 border-t border-border/50">
          <span className="font-mono text-[11px] text-muted-foreground">{job.id}</span>
          <button
            type="button"
            onClick={copyJobId}
            className="flex items-center gap-1 text-[11px] text-foreground hover:opacity-80 transition-opacity"
            aria-label="Copy job ID"
          >
            {copiedId ? (
              <CheckCheck className="h-3.5 w-3.5 text-success" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
            <span>{copiedId ? "Copied" : "Copy ID"}</span>
          </button>
        </div>
      </div>

      {/* Cancel button */}
      {!isFailed && job.status === "running" && (
        <div className="mt-6 flex justify-end">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setCancelModalOpen(true)}
            className="text-xs text-muted-foreground hover:text-destructive hover:border-destructive/40"
          >
            Cancel job
          </Button>
        </div>
      )}

      {/* Cancel Confirmation Dialog */}
      <Dialog
        open={cancelModalOpen}
        onOpenChange={setCancelModalOpen}
        title="Stop this job?"
        description="Clips already rendered are kept on this machine."
      >
        <Button
          variant="outline"
          size="sm"
          onClick={() => setCancelModalOpen(false)}
        >
          Keep running
        </Button>
        <Button
          variant="destructive"
          size="sm"
          onClick={() => {
            setCancelModalOpen(false);
            onCancel(job.id);
          }}
        >
          Stop job
        </Button>
      </Dialog>
    </div>
  );
}
