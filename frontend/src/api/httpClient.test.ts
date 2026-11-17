/**
 * Tests for HttpApiClient: error mapping, upload, SSE events, PATCH flow.
 * Uses vi.stubGlobal to mock fetch and EventSource.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { HttpApiClient, ApiError, isFfmpegMissingError, isNetworkError } from "./httpClient";
import type { Job, Clip } from "./client";

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeOkResponse<T>(body: T, status = 200): Response {
  return {
    ok: true,
    status,
    statusText: "OK",
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

function makeErrorResponse(code: string, message: string, status = 400): Response {
  return {
    ok: false,
    status,
    statusText: "Bad Request",
    json: () => Promise.resolve({ code, message }),
  } as unknown as Response;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("HttpApiClient", () => {
  let client: HttpApiClient;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    client = new HttpApiClient();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // ── Error mapping ──────────────────────────────────────────────────────────

  it("maps no_speech error code to UI copy", async () => {
    fetchMock.mockResolvedValueOnce(
      makeErrorResponse("no_speech", "No speech found", 422),
    );
    await expect(client.listJobs()).rejects.toMatchObject({
      code: "no_speech",
      message: "No speech found in this video. OpenClip needs words to cut clips.",
    });
  });

  it("maps ffmpeg_missing error code to UI copy", async () => {
    fetchMock.mockResolvedValueOnce(
      makeErrorResponse("ffmpeg_missing", "FFmpeg not found", 500),
    );
    let err: unknown;
    try {
      await client.listJobs();
    } catch (e) {
      err = e;
    }
    expect(isFfmpegMissingError(err)).toBe(true);
    expect((err as ApiError).message).toContain("openclip doctor");
  });

  it("wraps network-level TypeError as ApiError with network_error code", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(client.listJobs()).rejects.toMatchObject({
      code: "network_error",
    });
  });

  it("isNetworkError returns true for TypeError with fetch message", () => {
    expect(isNetworkError(new TypeError("Failed to fetch"))).toBe(true);
    expect(isNetworkError(new ApiError("network_error", "Cannot reach OpenClip"))).toBe(false);
  });

  it("throws ApiError for unknown error code with raw message", async () => {
    fetchMock.mockResolvedValueOnce(
      makeErrorResponse("some_new_code", "Something went wrong", 500),
    );
    await expect(client.listJobs()).rejects.toMatchObject({
      code: "some_new_code",
      message: "Something went wrong",
    });
  });

  // ── Happy-path methods ─────────────────────────────────────────────────────

  it("listJobs returns array on success", async () => {
    const jobs: Job[] = [];
    fetchMock.mockResolvedValueOnce(makeOkResponse(jobs));
    const result = await client.listJobs();
    expect(result).toEqual(jobs);
  });

  it("deleteJob returns undefined on 204", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 204,
      statusText: "No Content",
      json: () => Promise.reject(new Error("no body")),
    } as unknown as Response);
    const result = await client.deleteJob("job-1");
    expect(result).toBeUndefined();
  });

  // ── updateClip (PATCH flow) ────────────────────────────────────────────────

  it("updateClip sends PATCH with correct body and returns updated clip", async () => {
    const updatedClip: Clip = {
      id: "clip-1",
      start: 2.0,
      end: 28.0,
      score: 85,
      title: "Updated title",
      hook: "hook",
      hashtags: [],
      rationale: "reason",
      source: "heuristic",
      output_file: null,
      thumbnail: null,
    };
    fetchMock.mockResolvedValueOnce(makeOkResponse(updatedClip));

    const result = await client.updateClip("job-1", "clip-1", {
      start: 2.0,
      end: 28.0,
      caption_style_id: "clean-pop",
    });

    expect(result).toEqual(updatedClip);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/jobs/job-1/clips/clip-1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ start: 2.0, end: 28.0, caption_style_id: "clean-pop" }),
      }),
    );
  });

  // ── URL helpers ────────────────────────────────────────────────────────────

  it("getThumbnailUrl returns correct path", () => {
    expect(client.getThumbnailUrl("job-1", "clip-1")).toBe(
      "/api/jobs/job-1/clips/clip-1/thumbnail",
    );
  });

  it("getDownloadZipUrl returns correct path", () => {
    expect(client.getDownloadZipUrl("job-1")).toBe("/api/jobs/job-1/download.zip");
  });

  it("getCaptionsUrl returns correct path", () => {
    expect(client.getCaptionsUrl("job-1", "clip-1")).toBe(
      "/api/jobs/job-1/clips/clip-1/captions.ass",
    );
  });
});

// ── SSE event reducer tests ────────────────────────────────────────────────────

describe("subscribeJobEvents SSE event handling", () => {
  it("calls onProgress when progress event is dispatched", () => {
    const client = new HttpApiClient();
    const progressData = {
      job_id: "job-1",
      stage: "transcribe" as const,
      progress: 0.4,
      message: "Transcribing",
    };

    // Mock EventSource
    const listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
    let onerrorHandler: (() => void) | null = null;

    class MockES {
      static OPEN = 1;
      readyState = 1;
      addEventListener(type: string, fn: (e: MessageEvent) => void) {
        (listeners[type] ??= []).push(fn);
      }
      set onerror(fn: () => void) {
        onerrorHandler = fn;
      }
      close() { /* noop */ }
    }

    vi.stubGlobal("EventSource", MockES);

    const onProgress = vi.fn();
    client.subscribeJobEvents("job-1", onProgress);

    // Simulate a progress SSE event
    const event = new MessageEvent("progress", {
      data: JSON.stringify(progressData),
    });
    listeners["progress"]?.[0]?.(event);

    expect(onProgress).toHaveBeenCalledWith(progressData);
    // onerror handler is set by the client
    expect(onerrorHandler).not.toBeNull();

    vi.unstubAllGlobals();
  });

  it("calls onClipReady when clip_ready event arrives", () => {
    const client = new HttpApiClient();
    const clip: Clip = {
      id: "clip-1",
      start: 0,
      end: 30,
      score: 90,
      title: "Test",
      hook: "h",
      hashtags: [],
      rationale: "r",
      source: "heuristic",
      output_file: null,
      thumbnail: null,
    };

    const listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
    class MockES {
      addEventListener(type: string, fn: (e: MessageEvent) => void) {
        (listeners[type] ??= []).push(fn);
      }
      set onerror(_fn: () => void) { /* noop */ }
      close() { /* noop */ }
    }
    vi.stubGlobal("EventSource", MockES);

    const onClipReady = vi.fn();
    client.subscribeJobEvents("job-1", vi.fn(), onClipReady);

    listeners["clip_ready"]?.[0]?.(new MessageEvent("clip_ready", { data: JSON.stringify(clip) }));
    expect(onClipReady).toHaveBeenCalledWith(clip);

    vi.unstubAllGlobals();
  });

  it("unwraps the {clip: ...} envelope the backend sends for clip_ready", () => {
    const client = new HttpApiClient();
    const clip: Clip = {
      id: "clip-9",
      start: 1,
      end: 20,
      score: 70,
      title: "Wrapped",
      hook: "h",
      hashtags: [],
      rationale: "r",
      source: "llm",
      output_file: null,
      thumbnail: null,
    };

    const listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
    class MockES {
      addEventListener(type: string, fn: (e: MessageEvent) => void) {
        (listeners[type] ??= []).push(fn);
      }
      set onerror(_fn: () => void) { /* noop */ }
      close() { /* noop */ }
    }
    vi.stubGlobal("EventSource", MockES);

    const onClipReady = vi.fn();
    client.subscribeJobEvents("job-1", vi.fn(), onClipReady);

    listeners["clip_ready"]?.[0]?.(
      new MessageEvent("clip_ready", { data: JSON.stringify({ clip }) }),
    );
    expect(onClipReady).toHaveBeenCalledWith(clip);

    vi.unstubAllGlobals();
  });

  it("calls onDone and closes when done event fires", () => {
    const client = new HttpApiClient();
    const listeners: Record<string, ((...args: unknown[]) => void)[]> = {};
    let closed = false;

    class MockES {
      addEventListener(type: string, fn: (...args: unknown[]) => void) {
        (listeners[type] ??= []).push(fn);
      }
      set onerror(_fn: () => void) { /* noop */ }
      close() { closed = true; }
    }
    vi.stubGlobal("EventSource", MockES);

    const onDone = vi.fn();
    client.subscribeJobEvents("job-1", vi.fn(), undefined, onDone);
    listeners["done"]?.[0]?.();

    expect(onDone).toHaveBeenCalled();
    expect(closed).toBe(true);

    vi.unstubAllGlobals();
  });

  it("unsubscribe function closes the EventSource", () => {
    const client = new HttpApiClient();
    let closed = false;

    class MockES {
      addEventListener() { /* noop */ }
      set onerror(_fn: () => void) { void _fn; }
      close() { closed = true; }
    }
    vi.stubGlobal("EventSource", MockES);

    const unsub = client.subscribeJobEvents("job-1", vi.fn());
    expect(closed).toBe(false);
    unsub();
    expect(closed).toBe(true);

    vi.unstubAllGlobals();
  });
});
