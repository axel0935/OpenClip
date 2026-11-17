/**
 * HttpApiClient — connects the UI to the real OpenClip backend.
 *
 * Error handling: every non-2xx response is parsed as ErrorResponse{code,message}
 * and thrown as ApiError. UI_SPEC §8 messages are applied for known codes.
 */
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
  UploadResponse,
} from "./client";

// ── Error types ───────────────────────────────────────────────────────────────

/** UI_SPEC §8 error messages keyed by backend error code. */
const UI_ERROR_MESSAGES: Record<string, string> = {
  no_speech:
    "No speech found in this video. OpenClip needs words to cut clips.",
  ffmpeg_missing:
    "FFmpeg is not installed. Run `openclip doctor` in a terminal.",
  disk_full: "Not enough space in the data folder. Free 2 GB and retry.",
  unsupported_format:
    "OpenClip reads MP4, MOV, MKV, WebM and M4V. Convert this file first.",
  not_implemented: "This feature is not yet available in this build.",
};

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly httpStatus?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Returns true when the error code suggests FFmpeg is missing. */
export function isFfmpegMissingError(err: unknown): boolean {
  return err instanceof ApiError && err.code === "ffmpeg_missing";
}

/** Returns true when a backend error/job code suggests FFmpeg is missing. */
export function isFfmpegMissingCode(code: string | null | undefined): boolean {
  return code === "ffmpeg_missing";
}

/** Returns true when the backend is completely unreachable (network error). */
export function isNetworkError(err: unknown): boolean {
  return (
    err instanceof TypeError && err.message.toLowerCase().includes("fetch")
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function mapErrorResponse(code: string, rawMessage: string): string {
  return UI_ERROR_MESSAGES[code] ?? rawMessage;
}

async function parseErrorResponse(response: Response): Promise<ApiError> {
  let code = "unknown";
  let message = `Request failed: ${response.status} ${response.statusText}`;
  try {
    const body = (await response.json()) as {
      code?: string;
      message?: string;
      detail?: string;
    };
    if (body.code) code = body.code;
    if (body.message) message = mapErrorResponse(code, body.message);
    else if (body.detail) message = body.detail;
  } catch {
    // keep defaults
  }
  return new ApiError(code, message, response.status);
}

// ── Client ────────────────────────────────────────────────────────────────────

export class HttpApiClient implements ApiClient {
  private readonly baseUrl: string;

  constructor(baseUrl: string = "") {
    this.baseUrl = baseUrl;
  }

  // ── Core fetch helper ──────────────────────────────────────────────────────

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    let response: Response;
    try {
      response = await fetch(url, {
        ...init,
        headers: {
          Accept: "application/json",
          ...init?.headers,
        },
      });
    } catch {
      // Network-level failure (backend not running, CORS, etc.)
      throw new ApiError(
        "network_error",
        "Cannot reach OpenClip. Is `openclip serve` running?",
      );
    }

    if (!response.ok) {
      throw await parseErrorResponse(response);
    }

    if (response.status === 204) {
      return undefined as unknown as T;
    }

    return (await response.json()) as T;
  }

  // ── API methods ────────────────────────────────────────────────────────────

  async getHealth(): Promise<HealthResponse> {
    return this.request<HealthResponse>("/health");
  }

  async listCaptionStyles(): Promise<CaptionStyle[]> {
    return this.request<CaptionStyle[]>("/api/caption-styles");
  }

  /**
   * Upload a file with XHR so we get real upload progress (0–100).
   * onProgress is optional and called with percentage values.
   */
  createUpload(
    file: File,
    onProgress?: (pct: number) => void,
  ): Promise<UploadResponse> {
    return new Promise<UploadResponse>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${this.baseUrl}/api/uploads`);
      xhr.responseType = "json";

      if (onProgress) {
        xhr.upload.addEventListener("progress", (e) => {
          if (e.lengthComputable) {
            onProgress(Math.round((e.loaded / e.total) * 100));
          }
        });
      }

      xhr.addEventListener("load", () => {
        if (xhr.status === 201 || xhr.status === 200) {
          resolve(xhr.response as UploadResponse);
        } else {
          let code = "upload_failed";
          let msg = `Upload failed with status ${xhr.status}`;
          try {
            const body = xhr.response as { code?: string; message?: string };
            if (body.code) code = body.code;
            if (body.message) msg = mapErrorResponse(code, body.message);
          } catch {
            // keep defaults
          }
          reject(new ApiError(code, msg, xhr.status));
        }
      });

      xhr.addEventListener("error", () => {
        reject(
          new ApiError(
            "network_error",
            "Cannot reach OpenClip. Is `openclip serve` running?",
          ),
        );
      });

      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    });
  }

  async createJob(jobCreate: JobCreate): Promise<Job> {
    return this.request<Job>("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(jobCreate),
    });
  }

  async listJobs(): Promise<Job[]> {
    return this.request<Job[]>("/api/jobs");
  }

  async getJob(jobId: string): Promise<Job> {
    return this.request<Job>(`/api/jobs/${encodeURIComponent(jobId)}`);
  }

  async deleteJob(jobId: string): Promise<void> {
    return this.request<void>(`/api/jobs/${encodeURIComponent(jobId)}`, {
      method: "DELETE",
    });
  }

  async updateClip(
    jobId: string,
    clipId: string,
    update: ClipUpdate,
  ): Promise<Clip> {
    return this.request<Clip>(
      `/api/jobs/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(clipId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(update),
      },
    );
  }

  // ── URL helpers ────────────────────────────────────────────────────────────

  getVideoUrl(jobId: string, clipId: string): string {
    return `${this.baseUrl}/api/jobs/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(clipId)}/video`;
  }

  getThumbnailUrl(jobId: string, clipId: string): string {
    return `${this.baseUrl}/api/jobs/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(clipId)}/thumbnail`;
  }

  getPreviewUrl(jobId: string, clipId: string): string {
    return `${this.baseUrl}/api/jobs/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(clipId)}/preview`;
  }

  getCaptionsUrl(jobId: string, clipId: string): string {
    return `${this.baseUrl}/api/jobs/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(clipId)}/captions.ass`;
  }

  getDownloadZipUrl(jobId: string): string {
    return `${this.baseUrl}/api/jobs/${encodeURIComponent(jobId)}/download.zip`;
  }

  // ── SSE subscription with auto-reconnect and polling fallback ─────────────

  subscribeJobEvents(
    jobId: string,
    onProgress: (event: JobProgressEvent) => void,
    onClipReady?: (clip: Clip) => void,
    onDone?: () => void,
    onError?: (error: JobError) => void,
  ): () => void {
    // Capture baseUrl as a local constant for all closures
    const baseUrl = this.baseUrl;

    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;
    let consecutiveErrors = 0;

    const POLL_INTERVAL_MS = 1500;
    const MAX_SSE_ERRORS = 3; // fall back to polling after this many errors

    function cleanup(): void {
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      if (pollTimer !== null) clearTimeout(pollTimer);
      reconnectTimer = null;
      pollTimer = null;
      if (es) {
        es.close();
        es = null;
      }
    }

    // ── Polling fallback (used after repeated SSE failures) ──────────────────
    function startPolling(): void {
      if (closed) return;
      function poll(): void {
        if (closed) return;
        fetch(`${baseUrl}/api/jobs/${encodeURIComponent(jobId)}`, {
          headers: { Accept: "application/json" },
        })
          .then((r) => r.json())
          .then((job: Job) => {
            if (closed) return;
            onProgress({
              job_id: jobId,
              stage: job.stage ?? "ingest",
              progress: job.progress ?? 0,
              message: job.message ?? "",
            });
            if (job.status === "completed") {
              (job.clips ?? []).forEach((c) => onClipReady?.(c));
              onDone?.();
              cleanup();
              return;
            }
            if (job.status === "failed") {
              onError?.(job.error ?? { code: "unknown", message: "Job failed" });
              cleanup();
              return;
            }
            pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
          })
          .catch(() => {
            if (!closed) pollTimer = setTimeout(poll, POLL_INTERVAL_MS * 2);
          });
      }
      pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
    }

    // ── SSE connection ────────────────────────────────────────────────────────
    function connect(): void {
      if (closed) return;
      es = new EventSource(
        `${baseUrl}/api/jobs/${encodeURIComponent(jobId)}/events`,
      );

      es.addEventListener("progress", (e: MessageEvent) => {
        consecutiveErrors = 0;
        try {
          const data = JSON.parse(e.data) as JobProgressEvent;
          onProgress(data);
        } catch {
          // ignore malformed events
        }
      });

      es.addEventListener("clip_ready", (e: MessageEvent) => {
        consecutiveErrors = 0;
        try {
          const payload = JSON.parse(e.data) as unknown;
          // The backend wraps the clip: {"clip": {...}}. Tolerate a bare Clip too.
          const clip = (
            payload !== null &&
            typeof payload === "object" &&
            "clip" in payload
              ? (payload as { clip: Clip }).clip
              : payload
          ) as Clip;
          if (clip && typeof clip.id === "string") {
            onClipReady?.(clip);
          }
        } catch {
          // ignore
        }
      });

      es.addEventListener("done", () => {
        consecutiveErrors = 0;
        onDone?.();
        cleanup();
      });

      // Named "error" event from the server (not a connection error)
      es.addEventListener("error", (e: MessageEvent) => {
        try {
          if (e.data) {
            const err = JSON.parse(e.data) as JobError;
            onError?.(err);
          }
        } catch {
          // ignore
        }
        cleanup();
      });

      // Connection-level error (network drop, backend restart, etc.)
      es.onerror = () => {
        consecutiveErrors++;
        es?.close();
        es = null;
        if (closed) return;
        if (consecutiveErrors >= MAX_SSE_ERRORS) {
          // SSE seems persistently broken — fall back to polling
          startPolling();
          return;
        }
        // Exponential back-off reconnect
        const backoff = Math.min(500 * 2 ** consecutiveErrors, 8000);
        reconnectTimer = setTimeout(connect, backoff);
      };
    }

    connect();

    return () => {
      closed = true;
      cleanup();
    };
  }
}
