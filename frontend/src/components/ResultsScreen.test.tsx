import { act, render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ResultsScreen } from "./ResultsScreen";
import { ToastProvider } from "./ui/toast";
import { MockApiClient, generateMockClips } from "@/api/mockClient";
import type { Job } from "@/api/client";

describe("ResultsScreen", () => {
  const mockClips = generateMockClips();
  const mockJob: Job = {
    id: "job-test-123",
    status: "completed",
    stage: "export",
    progress: 1.0,
    message: "Completed",
    source: { type: "url", value: "https://youtube.com/watch?v=123" },
    options: {
      min_clip_seconds: 20,
      max_clip_seconds: 90,
      max_clips: 8,
      caption_style_id: "clean-pop",
      layout: "auto",
      whisper_profile: "auto",
      analysis_mode: "auto",
    },
    media: {
      path: "test.mp4",
      duration: 3600,
      width: 1920,
      height: 1080,
      fps: 30,
      has_audio: true,
      title: "Test Interview Video",
      source_url: null,
    },
    clips: mockClips,
    error: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };

  it("renders 9:16 clip cards with score badges and durations", async () => {
    const client = new MockApiClient();
    const presets = await client.listCaptionStyles();
    const onNewVideo = vi.fn();
    const onUpdateClip = vi.fn();

    render(
      <ToastProvider>
        <ResultsScreen
          job={mockJob}
          captionStyles={presets}
          onNewVideo={onNewVideo}
          onUpdateClip={onUpdateClip}
        />
      </ToastProvider>,
    );

    expect(screen.getByText("Test Interview Video")).toBeInTheDocument();
    // "6 clips" appears in both the visible header and the sr-only live region
    expect(screen.getAllByText(/6 clips/).length).toBeGreaterThanOrEqual(1);

    // Verify clips titles
    expect(screen.getByText("The single thing that changed everything")).toBeInTheDocument();
    expect(screen.getByText("Why most advice actually fails")).toBeInTheDocument();

    // Check score badges with tier text
    expect(screen.getByText("96 Strong")).toBeInTheDocument();
    expect(screen.getByText("88 Strong")).toBeInTheDocument();
    expect(screen.getByText("55 Good")).toBeInTheDocument();
  });

  it("sorts clips by score, longest, shortest, and source order", async () => {
    const client = new MockApiClient();
    const presets = await client.listCaptionStyles();
    const onNewVideo = vi.fn();
    const onUpdateClip = vi.fn();

    render(
      <ToastProvider>
        <ResultsScreen
          job={mockJob}
          captionStyles={presets}
          onNewVideo={onNewVideo}
          onUpdateClip={onUpdateClip}
        />
      </ToastProvider>,
    );

    const sortSelect = screen.getByLabelText("Sort clips");

    // By default score descending: top is score 96
    let headings = screen.getAllByRole("heading", { level: 3 });
    expect(headings[0]).toHaveTextContent("The single thing that changed everything");

    // Sort by Shortest (clip-4 is 22s)
    fireEvent.change(sortSelect, { target: { value: "duration_asc" } });
    headings = screen.getAllByRole("heading", { level: 3 });
    expect(headings[0]).toHaveTextContent("How to spot the trap before you fall in");

    // Sort by Longest (clip-5 is 85s)
    fireEvent.change(sortSelect, { target: { value: "duration_desc" } });
    headings = screen.getAllByRole("heading", { level: 3 });
    expect(headings[0]).toHaveTextContent("Behind the scenes of our biggest launch");

    // Sort by Source order (clip-1 starts at 14.0s)
    fireEvent.change(sortSelect, { target: { value: "source_order" } });
    headings = screen.getAllByRole("heading", { level: 3 });
    expect(headings[0]).toHaveTextContent("The single thing that changed everything");
  });

  it("opens detail sheet and supports trim nudging", async () => {
    const client = new MockApiClient();
    const presets = await client.listCaptionStyles();
    const onNewVideo = vi.fn();
    const onUpdateClip = vi.fn().mockResolvedValue({
      ...mockClips[0],
      start: 14.5,
      end: 52.0,
    });

    render(
      <ToastProvider>
        <ResultsScreen
          job={mockJob}
          captionStyles={presets}
          onNewVideo={onNewVideo}
          onUpdateClip={onUpdateClip}
        />
      </ToastProvider>,
    );

    // Click first card
    const firstCard = screen.getByText("The single thing that changed everything");
    fireEvent.click(firstCard);

    // Detail drawer opens
    expect(screen.getByText("Preview only — captions are burned in on re-render.")).toBeInTheDocument();

    // Nudge trim start +0.1s
    const nudgeButton = screen.getAllByText("+0.1s")[0];
    fireEvent.click(nudgeButton);

    // Check re-render button
    const reRenderButton = screen.getByRole("button", { name: /Re-render clip/i });
    expect(reRenderButton).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(reRenderButton);
      // Let the PATCH promise and the post-success timers settle.
      await new Promise((r) => setTimeout(r, 400));
    });

    expect(onUpdateClip).toHaveBeenCalledWith("clip-1", expect.objectContaining({
      start: 14.1,
      end: 52.0,
    }));
  });
});
