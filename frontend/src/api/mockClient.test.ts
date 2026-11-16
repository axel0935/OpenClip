import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { MockApiClient } from "./mockClient";

describe("MockApiClient state machine", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("loads 8 caption style presets", async () => {
    const client = new MockApiClient();
    const styles = await client.listCaptionStyles();
    expect(styles).toHaveLength(8);
    const ids = styles.map((s) => s.id);
    expect(ids).toContain("clean-pop");
    expect(ids).toContain("bold-impact");
    expect(ids).toContain("karaoke-glow");
  });

  it("creates a job and progresses through stages to completion with 6 clips", async () => {
    const client = new MockApiClient();
    const job = await client.createJob({
      source: { type: "url", value: "https://youtube.com/watch?v=test" },
      options: {
        min_clip_seconds: 20,
        max_clip_seconds: 90,
        max_clips: 8,
        caption_style_id: "clean-pop",
        layout: "auto",
        whisper_profile: "auto",
        analysis_mode: "auto",
      },
    });

    expect(job.status).toBe("running");
    expect(job.stage).toBe("ingest");

    // Advance to transcribe
    vi.advanceTimersByTime(2000);
    let current = await client.getJob(job.id);
    expect(current.stage).toBe("ingest");

    vi.advanceTimersByTime(3500);
    current = await client.getJob(job.id);
    expect(current.stage).toBe("transcribe");

    // Advance full schedule (~25s)
    vi.advanceTimersByTime(25000);
    current = await client.getJob(job.id);
    expect(current.status).toBe("completed");
    expect(current.progress).toBe(1.0);
    expect(current.clips).toHaveLength(6);
    expect(current.clips?.[0].score).toBe(96);
  });

  it("handles cancel via deleteJob", async () => {
    const client = new MockApiClient();
    const job = await client.createJob({
      source: { type: "url", value: "https://youtube.com/watch?v=cancel-test" },
      options: {
        min_clip_seconds: 20,
        max_clip_seconds: 90,
        max_clips: 8,
        caption_style_id: "clean-pop",
        layout: "auto",
        whisper_profile: "auto",
        analysis_mode: "auto",
      },
    });

    vi.advanceTimersByTime(2000);
    await client.deleteJob(job.id);

    const cancelled = await client.getJob(job.id);
    expect(cancelled.status).toBe("cancelled");

    // Advancing further should not complete the job
    vi.advanceTimersByTime(30000);
    const finalJob = await client.getJob(job.id);
    expect(finalJob.status).toBe("cancelled");
  });

  it("fails with no_speech error if ?mock=error is present in URL", async () => {
    // Mock window.location.search
    const originalLocation = window.location;
    // @ts-expect-error mocking window.location
    delete window.location;
    window.location = {
      ...originalLocation,
      search: "?mock=error",
    };

    const client = new MockApiClient();
    const job = await client.createJob({
      source: { type: "url", value: "https://youtube.com/watch?v=error-test" },
      options: {
        min_clip_seconds: 20,
        max_clip_seconds: 90,
        max_clips: 8,
        caption_style_id: "clean-pop",
        layout: "auto",
        whisper_profile: "auto",
        analysis_mode: "auto",
      },
    });

    // Advance to transcribe step where error triggers
    vi.advanceTimersByTime(8000);
    const failedJob = await client.getJob(job.id);
    expect(failedJob.status).toBe("failed");
    expect(failedJob.error?.code).toBe("no_speech");

    // Restore location
    window.location = originalLocation;
  });

  it("updates clip trim boundaries", async () => {
    const client = new MockApiClient();
    const job = await client.createJob({
      source: { type: "url", value: "https://youtube.com/watch?v=trim-test" },
      options: {
        min_clip_seconds: 20,
        max_clip_seconds: 90,
        max_clips: 8,
        caption_style_id: "clean-pop",
        layout: "auto",
        whisper_profile: "auto",
        analysis_mode: "auto",
      },
    });

    vi.advanceTimersByTime(25000);
    const clip = await client.updateClip(job.id, "clip-1", {
      start: 16.5,
      end: 45.0,
      caption_style_id: "bold-impact",
    });

    expect(clip.start).toBe(16.5);
    expect(clip.end).toBe(45.0);
  });
});
