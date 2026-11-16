import type {
  ApiClient,
  CaptionStyle,
  Clip,
  ClipUpdate,
  HealthResponse,
  Job,
  JobCreate,
  JobError,
  JobProgressEvent,
  MediaInfo,
  Stage,
  UploadResponse,
} from "./client";

function createSvgThumbnail(title: string, score: number, color1: string, color2: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1920" width="1080" height="1920">
    <defs>
      <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="${color1}" />
        <stop offset="100%" stop-color="${color2}" />
      </linearGradient>
    </defs>
    <rect width="1080" height="1920" fill="url(#g)" />
    <circle cx="540" cy="800" r="140" fill="rgba(255,255,255,0.18)" />
    <polygon points="510,730 610,800 510,870" fill="white" opacity="0.95" />
    <text x="540" y="1120" font-family="Inter, system-ui, sans-serif" font-size="52" font-weight="700" fill="white" text-anchor="middle" opacity="0.95">${title.slice(0, 32)}</text>
    <text x="540" y="1200" font-family="Inter, system-ui, sans-serif" font-size="36" font-weight="600" fill="rgba(255,255,255,0.75)" text-anchor="middle">Score ${score} · OpenClip</text>
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

export function generateMockClips(): Clip[] {
  return [
    {
      id: "clip-1",
      start: 14.0,
      end: 52.0,
      score: 96.0,
      title: "The single thing that changed everything",
      hook: "If you only do one thing this year...",
      hashtags: ["#growth", "#mindset", "#productivity"],
      rationale: "High energy spike with strong opening sentence and clear takeaway.",
      source: "heuristic",
      output_file: "clip-1.mp4",
      thumbnail: createSvgThumbnail("The single thing", 96, "#1A2A6C", "#FDBB2D"),
    },
    {
      id: "clip-2",
      start: 82.0,
      end: 109.0,
      score: 88.0,
      title: "Why most advice actually fails",
      hook: "Stop doing what the top 1% say they do.",
      hashtags: ["#truth", "#career", "#strategy"],
      rationale: "Contrarian hook backed by rapid speech cadence and question prompt.",
      source: "heuristic",
      output_file: "clip-2.mp4",
      thumbnail: createSvgThumbnail("Why most advice fails", 88, "#283048", "#859398"),
    },
    {
      id: "clip-3",
      start: 140.0,
      end: 205.0,
      score: 82.0,
      title: "The secret framework explained simply",
      hook: "This 3-step loop took 5 years to figure out.",
      hashtags: ["#framework", "#learn", "#skills"],
      rationale: "Consistent sustained engagement with high audio energy and clear structure.",
      source: "heuristic",
      output_file: "clip-3.mp4",
      thumbnail: createSvgThumbnail("Secret framework", 82, "#0F2027", "#2C5364"),
    },
    {
      id: "clip-4",
      start: 240.0,
      end: 262.0,
      score: 74.0,
      title: "How to spot the trap before you fall in",
      hook: "Watch out for this subtle warning sign.",
      hashtags: ["#lessons", "#advice", "#mistakes"],
      rationale: "Intense warning hook with good face framing and punchy pacing.",
      source: "heuristic",
      output_file: "clip-4.mp4",
      thumbnail: createSvgThumbnail("Spot the trap", 74, "#2C3E50", "#4CA1AF"),
    },
    {
      id: "clip-5",
      start: 310.0,
      end: 395.0,
      score: 65.0,
      title: "Behind the scenes of our biggest launch",
      hook: "Nobody saw what happened 24 hours prior.",
      hashtags: ["#founder", "#stories", "#bts"],
      rationale: "Storytelling clip with moderate pacing and good camera switches.",
      source: "heuristic",
      output_file: "clip-5.mp4",
      thumbnail: createSvgThumbnail("Behind the scenes", 65, "#1F1C2C", "#928DAB"),
    },
    {
      id: "clip-6",
      start: 430.0,
      end: 475.0,
      score: 55.0,
      title: "A quick counterintuitive observation",
      hook: "Have you ever noticed this weird pattern?",
      hashtags: ["#thoughts", "#perspective", "#life"],
      rationale: "Casual thought segment, lower question rate but good closing insight.",
      source: "heuristic",
      output_file: "clip-6.mp4",
      thumbnail: createSvgThumbnail("Counterintuitive", 55, "#3E5151", "#DECBA4"),
    },
  ];
}

interface StageStep {
  stage: Stage;
  progress: number;
  message: string;
  delayMs: number;
}

const STAGE_SCHEDULE: StageStep[] = [
  { stage: "ingest", progress: 0.08, message: "Reading duration, audio, frame rate", delayMs: 1500 },
  { stage: "transcribe", progress: 0.22, message: "Word-level timing, English", delayMs: 3000 },
  { stage: "segment", progress: 0.38, message: "Finding candidate windows", delayMs: 2500 },
  { stage: "score", progress: 0.48, message: "Scoring hooks, energy, questions", delayMs: 2500 },
  { stage: "select", progress: 0.58, message: "Selecting top highlight clips", delayMs: 2000 },
  { stage: "reframe", progress: 0.72, message: "Tracking faces, smoothing the crop", delayMs: 3000 },
  { stage: "captions", progress: 0.82, message: "Generating dynamic burned-in captions", delayMs: 2000 },
  { stage: "render", progress: 0.92, message: "Burning captions into 1080×1920", delayMs: 2500 },
  { stage: "export", progress: 0.98, message: "Finalizing clips and thumbnails", delayMs: 1500 },
];

interface MockJobListener {
  onProgress: (event: JobProgressEvent) => void;
  onClipReady?: (clip: Clip) => void;
  onDone?: () => void;
  onError?: (error: JobError) => void;
}

export class MockApiClient implements ApiClient {
  private jobs: Map<string, Job> = new Map();
  private timers: Map<string, ReturnType<typeof setTimeout>[]> = new Map();
  private listeners: Map<string, Set<MockJobListener>> = new Map();
  private cachedStyles: CaptionStyle[] | null = null;

  async getHealth(): Promise<HealthResponse> {
    return { status: "ok", version: "0.1.0" };
  }

  async listCaptionStyles(): Promise<CaptionStyle[]> {
    if (this.cachedStyles) {
      return this.cachedStyles;
    }

    const modules = import.meta.glob<Record<string, unknown>>(
      "../../../presets/captions/*.json",
      { eager: true },
    );

    const styles: CaptionStyle[] = Object.values(modules).map((mod) => {
      const data = "default" in mod && mod.default ? mod.default : mod;
      return data as unknown as CaptionStyle;
    });

    this.cachedStyles = styles;
    return styles;
  }

  async createUpload(file: File): Promise<UploadResponse> {
    return {
      upload_id: `upload-${Date.now()}`,
      filename: file.name,
      size_bytes: file.size,
    };
  }

  async createJob(jobCreate: JobCreate): Promise<Job> {
    const id = `job-${Date.now().toString(36)}-${Math.random().toString(36).substring(2, 6)}`;
    const now = new Date().toISOString();

    const media: MediaInfo = {
      path: jobCreate.source.type === "upload" ? `uploads/${jobCreate.source.value}` : jobCreate.source.value,
      duration: 3600.0,
      width: 1920,
      height: 1080,
      fps: 30.0,
      has_audio: true,
      title:
        jobCreate.source.type === "url"
          ? "How Great Leaders Inspire Action"
          : "recorded-interview-ep42.mp4",
      source_url: jobCreate.source.type === "url" ? jobCreate.source.value : null,
    };

    const job: Job = {
      id,
      status: "running",
      stage: "ingest",
      progress: 0.05,
      message: "Getting the video",
      source: jobCreate.source,
      options: jobCreate.options,
      media,
      clips: [],
      error: null,
      created_at: now,
      updated_at: now,
    };

    this.jobs.set(id, job);
    this.startMockProgression(id);

    return job;
  }

  private startMockProgression(jobId: string): void {
    const shouldFail =
      typeof window !== "undefined" &&
      window.location &&
      window.location.search &&
      window.location.search.includes("mock=error");

    const timerIds: ReturnType<typeof setTimeout>[] = [];
    let accumulatedTime = 0;

    STAGE_SCHEDULE.forEach((step, index) => {
      accumulatedTime += step.delayMs;
      const t = setTimeout(() => {
        const currentJob = this.jobs.get(jobId);
        if (!currentJob || currentJob.status !== "running") return;

        if (shouldFail && index === 2) {
          const jobError: JobError = {
            code: "no_speech",
            message: "No speech found in this video. OpenClip needs words to cut clips.",
          };
          currentJob.status = "failed";
          currentJob.error = jobError;
          currentJob.message = jobError.message;
          currentJob.updated_at = new Date().toISOString();
          this.notifyProgress(jobId, {
            job_id: jobId,
            stage: step.stage,
            progress: currentJob.progress,
            message: jobError.message,
          });
          this.notifyError(jobId, jobError);
          return;
        }

        currentJob.stage = step.stage;
        currentJob.progress = step.progress;
        currentJob.message = step.message;
        currentJob.updated_at = new Date().toISOString();

        this.notifyProgress(jobId, {
          job_id: jobId,
          stage: step.stage,
          progress: step.progress,
          message: step.message,
        });
      }, accumulatedTime);

      timerIds.push(t);
    });

    const completionTime = accumulatedTime + 1000;
    const finalTimer = setTimeout(() => {
      const currentJob = this.jobs.get(jobId);
      if (!currentJob || currentJob.status !== "running") return;

      currentJob.status = "completed";
      currentJob.progress = 1.0;
      currentJob.message = "Completed";
      currentJob.clips = generateMockClips();
      currentJob.updated_at = new Date().toISOString();

      this.notifyProgress(jobId, {
        job_id: jobId,
        stage: "export",
        progress: 1.0,
        message: "Completed",
      });

      currentJob.clips.forEach((clip) => {
        this.notifyClipReady(jobId, clip);
      });
      this.notifyDone(jobId);
    }, completionTime);

    timerIds.push(finalTimer);
    this.timers.set(jobId, timerIds);
  }

  private notifyProgress(jobId: string, event: JobProgressEvent): void {
    const list = this.listeners.get(jobId);
    if (list) {
      list.forEach((l) => l.onProgress(event));
    }
  }

  private notifyClipReady(jobId: string, clip: Clip): void {
    const list = this.listeners.get(jobId);
    if (list) {
      list.forEach((l) => l.onClipReady?.(clip));
    }
  }

  private notifyDone(jobId: string): void {
    const list = this.listeners.get(jobId);
    if (list) {
      list.forEach((l) => l.onDone?.());
    }
  }

  private notifyError(jobId: string, error: JobError): void {
    const list = this.listeners.get(jobId);
    if (list) {
      list.forEach((l) => l.onError?.(error));
    }
  }

  async listJobs(): Promise<Job[]> {
    return Array.from(this.jobs.values()).sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );
  }

  async getJob(jobId: string): Promise<Job> {
    const job = this.jobs.get(jobId);
    if (!job) {
      throw new Error(`Job not found: ${jobId}`);
    }
    return job;
  }

  async deleteJob(jobId: string): Promise<void> {
    const job = this.jobs.get(jobId);
    if (job) {
      job.status = "cancelled";
      job.message = "Cancelled by user";
      job.updated_at = new Date().toISOString();
    }
    const timers = this.timers.get(jobId);
    if (timers) {
      timers.forEach((t) => clearTimeout(t));
      this.timers.delete(jobId);
    }
  }

  async updateClip(jobId: string, clipId: string, update: ClipUpdate): Promise<Clip> {
    const job = this.jobs.get(jobId);
    if (!job) {
      throw new Error(`Job not found: ${jobId}`);
    }
    const clips = job.clips ?? [];
    const clip = clips.find((c) => c.id === clipId);
    if (!clip) {
      throw new Error(`Clip not found: ${clipId}`);
    }

    if (update.start !== undefined && update.start !== null) {
      clip.start = update.start;
    }
    if (update.end !== undefined && update.end !== null) {
      clip.end = update.end;
    }
    job.updated_at = new Date().toISOString();
    return clip;
  }

  getVideoUrl(jobId: string, clipId: string): string {
    void jobId;
    void clipId;
    return "#";
  }

  getThumbnailUrl(jobId: string, clipId: string): string {
    const job = this.jobs.get(jobId);
    const clip = (job?.clips ?? []).find((c) => c.id === clipId);
    return clip?.thumbnail || createSvgThumbnail("Clip", 80, "#2C3E50", "#4CA1AF");
  }

  getPreviewUrl(jobId: string, clipId: string): string {
    void jobId;
    void clipId;
    return "#";
  }

  getCaptionsUrl(jobId: string, clipId: string): string {
    void jobId;
    void clipId;
    return "#";
  }

  getDownloadZipUrl(jobId: string): string {
    void jobId;
    return "#";
  }

  subscribeJobEvents(
    jobId: string,
    onProgress: (event: JobProgressEvent) => void,
    onClipReady?: (clip: Clip) => void,
    onDone?: () => void,
    onError?: (error: JobError) => void,
  ): () => void {
    if (!this.listeners.has(jobId)) {
      this.listeners.set(jobId, new Set());
    }
    const set = this.listeners.get(jobId)!;
    const listener: MockJobListener = { onProgress, onClipReady, onDone, onError };
    set.add(listener);

    return () => {
      set.delete(listener);
      if (set.size === 0) {
        this.listeners.delete(jobId);
      }
    };
  }
}
