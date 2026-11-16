import { useEffect, useSyncExternalStore } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { defaultApiClient, type Job, type JobCreate, type ClipUpdate } from "@/api/client";
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
  const match = hash.match(/^#\/(?:jobs?\/)?([^/?]+)/);
  if (match && match[1] && match[1] !== "") {
    return match[1];
  }
  return null;
}

export function MainApp() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const hash = useSyncExternalStore(subscribeHash, getHash, () => "#/");
  const activeJobId = parseJobIdFromHash(hash);

  // Queries
  const captionStylesQuery = useQuery({
    queryKey: ["caption-styles"],
    queryFn: () => defaultApiClient.listCaptionStyles(),
    staleTime: Infinity,
  });

  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: () => defaultApiClient.listJobs(),
    refetchInterval: activeJobId ? 2000 : false,
  });

  const currentJobQuery = useQuery({
    queryKey: ["job", activeJobId],
    queryFn: () => (activeJobId ? defaultApiClient.getJob(activeJobId) : null),
    enabled: Boolean(activeJobId),
    refetchInterval: (query) => {
      const data = query.state.data as Job | null | undefined;
      return data?.status === "running" ? 800 : false;
    },
  });

  // SSE subscription if available
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
          };
        });
      },
      (clip) => {
        queryClient.setQueryData(["job", activeJobId], (prev: Job | undefined) => {
          if (!prev) return prev;
          const currentClips = prev.clips ?? [];
          return {
            ...prev,
            clips: [...currentClips, clip],
          };
        });
      },
      () => {
        queryClient.invalidateQueries({ queryKey: ["job", activeJobId] });
        queryClient.invalidateQueries({ queryKey: ["jobs"] });
      },
    );
    return () => unsub();
  }, [activeJobId, queryClient]);

  // Mutations
  const createJobMutation = useMutation({
    mutationFn: (jobCreate: JobCreate) => defaultApiClient.createJob(jobCreate),
    onSuccess: (newJob) => {
      queryClient.setQueryData(["job", newJob.id], newJob);
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      navigateTo(`#/jobs/${newJob.id}`);
    },
    onError: (err: Error) => {
      toast(err.message || "Failed to start job", "destructive");
    },
  });

  const cancelJobMutation = useMutation({
    mutationFn: (jobId: string) => defaultApiClient.deleteJob(jobId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["job", activeJobId] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      toast("Job cancelled");
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
        return {
          ...prev,
          clips: nextClips,
        };
      });
    },
  });

  const handleNewVideo = () => {
    navigateTo("#/");
  };

  const handleSelectJob = (id: string) => {
    navigateTo(`#/jobs/${id}`);
  };

  const currentJob = currentJobQuery.data;
  const allJobs = jobsQuery.data || [];
  const captionStyles = captionStylesQuery.data || [];

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground antialiased selection:bg-primary selection:text-primary-foreground">
      {/* Skip to content accessibility link */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground focus:outline-none focus:ring-2 focus:ring-ring"
      >
        Skip to content
      </a>

      {/* Top Header Navigation */}
      <TopBar
        jobs={allJobs}
        currentJobId={activeJobId}
        onSelectJob={handleSelectJob}
        onNewVideo={handleNewVideo}
      />

      {/* Main Dynamic Viewport */}
      <main id="main-content" className="flex-1">
        {!activeJobId || !currentJob ? (
          <ImportScreen
            captionStyles={captionStyles}
            onSubmit={(req) => createJobMutation.mutate(req)}
            onUploadFile={(file) => defaultApiClient.createUpload(file)}
            isSubmitting={createJobMutation.isPending}
          />
        ) : currentJob.status === "completed" ? (
          <ResultsScreen
            job={currentJob}
            captionStyles={captionStyles}
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
