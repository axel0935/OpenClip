import type { components } from "./schema";
import { MockApiClient } from "./mockClient";
import { HttpApiClient } from "./httpClient";

export type CaptionStyle = components["schemas"]["CaptionStyle"];
export type UploadResponse = components["schemas"]["UploadResponse"];
export type JobCreate = components["schemas"]["JobCreate"];
export type Job = components["schemas"]["Job"];
export type JobStatus = components["schemas"]["JobStatus"];
export type Stage = components["schemas"]["Stage"];
export type JobError = components["schemas"]["JobError"];
export type Clip = components["schemas"]["Clip"];
export type ClipUpdate = components["schemas"]["ClipUpdate"];
export type HealthResponse = components["schemas"]["HealthResponse"];
export type MediaInfo = components["schemas"]["MediaInfo"];
export type JobOptions = components["schemas"]["JobOptions"];
export type JobSource = components["schemas"]["JobSource"];

/** Human-readable job title: media title, else the source file's basename. */
export function jobTitle(job: Job): string {
  if (job.media?.title) return job.media.title;
  const value = job.source.value;
  const base = value.split(/[\\/]/).pop();
  return base && base.length > 0 ? base : value;
}

/**
 * Progress event payload. The HTTP backend sends `_job_event` shape
 * ({id, status, stage, progress, message}); the contract doc and the mock
 * client use `job_id`. All fields are optional-tolerant so both work.
 */
export interface JobProgressEvent {
  job_id?: string;
  id?: string;
  status?: string;
  stage: Stage | null;
  progress: number;
  message: string | null;
}

export interface ApiClient {
  getHealth(): Promise<HealthResponse>;
  listCaptionStyles(): Promise<CaptionStyle[]>;
  createUpload(file: File, onProgress?: (pct: number) => void): Promise<UploadResponse>;
  createJob(jobCreate: JobCreate): Promise<Job>;
  listJobs(): Promise<Job[]>;
  getJob(jobId: string): Promise<Job>;
  deleteJob(jobId: string): Promise<void>;
  updateClip(jobId: string, clipId: string, update: ClipUpdate): Promise<Clip>;
  getVideoUrl(jobId: string, clipId: string): string;
  getThumbnailUrl(jobId: string, clipId: string): string;
  getPreviewUrl(jobId: string, clipId: string): string;
  getCaptionsUrl(jobId: string, clipId: string): string;
  getDownloadZipUrl(jobId: string): string;
  subscribeJobEvents?(
    jobId: string,
    onProgress: (event: JobProgressEvent) => void,
    onClipReady?: (clip: Clip) => void,
    onDone?: () => void,
    onError?: (error: JobError) => void,
  ): () => void;
}

// Default to "http" (real backend). Use VITE_API_MODE=mock for demos/tests.
const apiMode = import.meta.env.VITE_API_MODE ?? "http";

export const defaultApiClient: ApiClient =
  apiMode === "mock" ? new MockApiClient() : new HttpApiClient();
