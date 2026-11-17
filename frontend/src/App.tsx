import { useEffect, useSyncExternalStore, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { defaultApiClient, type Job, type JobCreate, type ClipUpdate, type JobStatus } from "@/api/client";
import { ApiError, isFfmpegMissingError, isNetworkError } from "@/api/httpClient";
import { TopBar } from "@/components/TopBar";
import { ImportScreen } from "@/components/ImportScreen";
import { ProcessingScreen } from "@/components/ProcessingScreen";
import { ResultsScreen } from "@/components/ResultsScreen";
import { ToastProvider } from "@/components/ui/toast";
import { useToast } from "@/components/ui/use-toast";

function navigateTo(hash: string) {
  window.location.hash = hash;
  try {
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  } catch {
    window.dispatchEvent(new Event("hashchange"));
  }
}

function subscribeHash(callback: () => void) {
  window.addEventListener("hashchange", callback);
  return () => window.removeEventListener("hashchange", callback);
}

function getHash() {
  return window.location.hash || "#/";
}

function parseJobIdFromHash(hash: string): string | null {
  const match = hash.match(/^#\/(?:jobs?\/)?([\w-]+)/);
  if (match && match[1] && match[1] !== "") {
    return match[1];
  }
  return null;
}

const JOB_STATUSES = new Set<JobStatus>([
  "queued",
  "running",
  "completed",
  "failed",
  "cancelled",
]);

function asJobStatus(value: string | undefined): JobStatus | undefined {
  return value !== undefined && JOB_STATUSES.has(value as JobStatus)
    ? (value as JobStatus)
    : undefined;
}

/** Map an error from the API to a user-facing message string. */
function friendlyError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "An unexpected error occurred.";
}

export function MainApp() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const hash = useSyncExternalStore(subscribeHash, getHash, () => "#/");
  const activeJobId = parseJobIdFromHash(hash);

  // Backend-reachability state
  const [backendUnreachable, setBackendUnreachable] = useState(false);

  // Per-clip render version, bumped on every clip_ready SSE event. Used by
  // the results screen to cache-bust thumbnail/video/preview URLs.
  const [clipVersions, setClipVersions] = useState<Record<string, number>>({});

  // Queries
  const captionStylesQuery = useQuery({
    queryKey: ["caption-styles"],
    queryFn: () => defaultApiClient.listCaptionStyles(),
    staleTime: Infinity,
    retry: 2,
  });

  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: () => defaultApiClient.listJobs(),
    // Poll while a job is active so History updates
    refetchInterval: activeJobId ? 5000 : 10000,
    retry: 1,
    // Detect backend unreachability
    meta: {
      onError: (err: unknown) => {
        if (isNetworkError(err) || (err instanceof ApiError && err.code === "network_error")) {
          setBackendUnreachable(true);
        }
      },
    },
  });

  // Track backend reachability from query failures
  useEffect(() => {
    if (jobsQuery.isSuccess) setBackendUnreachable(false);
    if (jobsQuery.isError) {
      const err = jobsQuery.error;
      if (isNetworkError(err) || (err instanceof ApiError && err.code === "network_error")) {
        setBackendUnreachable(true);
      }
    }
  }, [jobsQuery.isSuccess, jobsQuery.isError, jobsQuery.error]);

  const currentJobQuery = useQuery({
    queryKey: ["job", activeJobId],
    queryFn: () => (activeJobId ? defaultApiClient.getJob(activeJobId) : null),
    enabled: Boolean(activeJobId),
    // Slower polling — SSE is the primary real-time channel
    refetchInterval: (query) => {
      const data = query.state.data as Job | null | undefined;
      return data?.status === "running" ? 3000 : false;
    },
    staleTime: 1000,
  });

  // SSE subscription — also covers resume when user navigates back to #/jobs/:id
  useEffect(() => {
    if (!activeJobId || !defaultApiClient.subscribeJobEvents) return;

    const unsub = defaultApiClient.subscribeJobEvents(
      activeJobId,
      (event) => {
        queryClient.setQueryData(["job", activeJobId], (prev: Job | undefined) => {
          if (!prev) return prev;
          return {
            ...prev,
            stage: event.stage,
            progress: event.progress,
            message: event.message,
            // Prefer the status carried by the event; fall back to inferring
            // "running" from the first progress frame of a queued job.
            status:
              asJobStatus(event.status) ??
              (prev.status === "queued" ? "running" : prev.status),
          };
        });
        // Reset backend-unreachable when SSE is working
        setBackendUnreachable(false);
      },
      (clip) => {
        // clip_ready — upsert into the job's clips and bump the media version
        // so thumbnail/video/preview URLs are refetched (new clip or re-render).
        setClipVersions((prev) => ({
          ...prev,
          [clip.id]: (prev[clip.id] ?? 0) + 1,
        }));
        queryClient.setQueryData(["job", activeJobId], (prev: Job | undefined) => {
          if (!prev) return prev;
          const existing = prev.clips ?? [];
          const index = existing.findIndex((c) => c.id === clip.id);
          const nextClips =
            index >= 0
              ? existing.map((c) => (c.id === clip.id ? clip : c))
              : [...existing, clip];
          return {
            ...prev,
            clips: nextClips,
          };
        });
      },
      () => {
        // done — invalidate to get the final completed state
        void queryClient.invalidateQueries({ queryKey: ["job", activeJobId] });
        void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      },
      (error) => {
        // Clip-scoped re-render failure: surface it without failing the job.
        const clipId = (error as { clip_id?: string }).clip_id;
        if (clipId) {
          toast(`Clip re-render failed: ${error.message}`, "destructive");
          return;
        }
        // SSE error event from server (job-level failure)
        queryClient.setQueryData(["job", activeJobId], (prev: Job | undefined) => {
          if (!prev) return prev;
          return {
            ...prev,
            status: "failed",
            error,
          };
        });
        void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      },
    );
    return () => unsub();
  }, [activeJobId, queryClient, toast]);

  // Mutations
  const createJobMutation = useMutation({
    mutationFn: (jobCreate: JobCreate) => defaultApiClient.createJob(jobCreate),
    onSuccess: (newJob) => {
      queryClient.setQueryData(["job", newJob.id], newJob);
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      navigateTo(`#/jobs/${newJob.id}`);
    },
    onError: (err: unknown) => {
      const msg = friendlyError(err);
      toast(
        isFfmpegMissingError(err)
          ? 'FFmpeg is not installed. Run `openclip doctor` in a terminal.'
          : msg,
        "destructive",
      );
    },
  });

  const cancelJobMutation = useMutation({
    mutationFn: (jobId: string) => defaultApiClient.deleteJob(jobId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["job", activeJobId] });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      toast("Job cancelled");
    },
    onError: (err: unknown) => {
      toast(friendlyError(err), "destructive");
    },
  });

  const deleteJobMutation = useMutation({
    mutationFn: (jobId: string) => defaultApiClient.deleteJob(jobId),
    onSuccess: (_data, jobId) => {
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      if (jobId === activeJobId) navigateTo("#/");
      toast("Job deleted");
    },
    onError: (err: unknown) => {
      toast(friendlyError(err), "destructive");
    },
  });

  const updateClipMutation = useMutation({
    mutationFn: ({ clipId, update }: { clipId: string; update: ClipUpdate }) => {
      if (!activeJobId) throw new Error("No active job");
      return defaultApiClient.updateClip(activeJobId, clipId, update);
    },
    onSuccess: (updatedClip) => {
      queryClient.setQueryData(["job", activeJobId], (prev: Job | undefined) => {
        if (!prev) return prev;
        const nextClips = (prev.clips ?? []).map((c) =>
          c.id === updatedClip.id ? updatedClip : c,
        );
        return { ...prev, clips: nextClips };
      });
      // The backend re-renders in the background; refetch so any server-side
      // clip fields (output_file, thumbnail) are refreshed.
      void queryClient.invalidateQueries({ queryKey: ["job", activeJobId] });
    },
    onError: (err: unknown) => {
      toast(friendlyError(err), "destructive");
    },
  });

  const handleNewVideo = () => navigateTo("#/");
  const handleSelectJob = (id: string) => navigateTo(`#/jobs/${id}`);

  const currentJob = currentJobQuery.data;
  const allJobs = jobsQuery.data ?? [];
  const captionStyles = captionStylesQuery.data ?? [];

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground antialiased selection:bg-primary selection:text-primary-foreground">
      {/* Skip to content accessibility link */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground focus:outline-none focus:ring-2 focus:ring-ring"
      >
        Skip to content
      </a>

      {/* Backend unreachable banner */}
      {backendUnreachable && (
        <div
          role="alert"
          aria-live="assertive"
          className="sticky top-0 z-50 flex items-center justify-center gap-2 bg-destructive px-4 py-2 text-xs font-medium text-white"
        >
          <span>Cannot reach OpenClip. Is <code>openclip serve</code> running?</span>
          {isFfmpegMissingError(jobsQuery.error) && (
            <span className="ml-2 opacity-80">· Run <code>openclip doctor</code> for details.</span>
          )}
        </div>
      )}

      {/* Top Header Navigation */}
      <TopBar
        jobs={allJobs}
        currentJobId={activeJobId}
        onSelectJob={handleSelectJob}
        onNewVideo={handleNewVideo}
        backendUnreachable={backendUnreachable}
        onDeleteJob={(jobId) => deleteJobMutation.mutate(jobId)}
      />

      {/* Main Dynamic Viewport */}
      <main id="main-content" className="flex-1">
        {!activeJobId || !currentJob ? (
          <ImportScreen
            captionStyles={captionStyles}
            onSubmit={(req) => createJobMutation.mutate(req)}
            onUploadFile={(file, onProgress) =>
              defaultApiClient.createUpload(file, onProgress)
            }
            isSubmitting={createJobMutation.isPending}
          />
        ) : currentJob.status === "completed" ? (
          <ResultsScreen
            job={currentJob}
            captionStyles={captionStyles}
            clipVersions={clipVersions}
            onNewVideo={handleNewVideo}
            onUpdateClip={(clipId, update) =>
              updateClipMutation.mutateAsync({ clipId, update })
            }
          />
        ) : (
          <ProcessingScreen
            job={currentJob}
            onCancel={(jobId) => cancelJobMutation.mutate(jobId)}
            onRetry={() => {
              if (currentJob) {
                createJobMutation.mutate({
                  source: currentJob.source,
                  options: currentJob.options,
                });
              }
            }}
          />
        )}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <MainApp />
    </ToastProvider>
  );
}
