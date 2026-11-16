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

export class HttpApiClient implements ApiClient {
  private baseUrl: string;

  constructor(baseUrl: string = "") {
    this.baseUrl = baseUrl;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const response = await fetch(url, {
      ...init,
      headers: {
        Accept: "application/json",
        ...init?.headers,
      },
    });

    if (!response.ok) {
      let errorMsg = `Request failed: ${response.status} ${response.statusText}`;
      try {
        const errorJson = (await response.json()) as { message?: string; detail?: string };
        if (errorJson.message) errorMsg = errorJson.message;
        else if (errorJson.detail) errorMsg = errorJson.detail;
      } catch {
        // use fallback
      }
      throw new Error(errorMsg);
    }

    if (response.status === 204) {
      return undefined as unknown as T;
    }

    return (await response.json()) as T;
  }

  async getHealth(): Promise<HealthResponse> {
    return this.request<HealthResponse>("/health");
  }

  async listCaptionStyles(): Promise<CaptionStyle[]> {
    return this.request<CaptionStyle[]>("/api/caption-styles");
  }

  async createUpload(file: File): Promise<UploadResponse> {
    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch(`${this.baseUrl}/api/uploads`, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      throw new Error(`Upload failed with status ${response.status}`);
    }

    return (await response.json()) as UploadResponse;
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

  async updateClip(jobId: string, clipId: string, update: ClipUpdate): Promise<Clip> {
    return this.request<Clip>(
      `/api/jobs/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(clipId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(update),
      },
    );
  }

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

  subscribeJobEvents(
    jobId: string,
    onProgress: (event: JobProgressEvent) => void,
    onClipReady?: (clip: Clip) => void,
    onDone?: () => void,
    onError?: (error: JobError) => void,
  ): () => void {
    const eventSource = new EventSource(
      `${this.baseUrl}/api/jobs/${encodeURIComponent(jobId)}/events`,
    );

    eventSource.addEventListener("progress", (e) => {
      try {
        const data = JSON.parse(e.data) as JobProgressEvent;
        onProgress(data);
      } catch {
        // ignore malformed
      }
    });

    if (onClipReady) {
      eventSource.addEventListener("clip_ready", (e) => {
        try {
          const data = JSON.parse(e.data) as Clip;
          onClipReady(data);
        } catch {
          // ignore
        }
      });
    }

    if (onDone) {
      eventSource.addEventListener("done", () => {
        onDone();
        eventSource.close();
      });
    }

    if (onError) {
      eventSource.addEventListener("error", (e) => {
        try {
          // @ts-expect-error SSE message data
          if (e.data) {
            // @ts-expect-error SSE message data
            const err = JSON.parse(e.data) as JobError;
            onError(err);
          }
        } catch {
          // ignore
        }
      });
    }

    eventSource.onerror = () => {
      eventSource.close();
    };

    return () => {
      eventSource.close();
    };
  }
}
