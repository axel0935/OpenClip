import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { expect, it, describe, vi, beforeEach, afterEach } from "vitest";
import App from "./App";

describe("OpenClip App Integration", () => {
  beforeEach(() => {
    window.location.hash = "#/";
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the OpenClip shell and import screen", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>,
    );

    expect(screen.getByText("OpenClip")).toBeInTheDocument();
    expect(screen.getByText("Paste a link, or drop a file.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "History" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
  });

  it("executes the full Import -> Processing -> Results flow with fake timers", async () => {
    vi.useFakeTimers();

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>,
    );

    // 1. Fill URL on Import screen
    const urlInput = screen.getByPlaceholderText("https://youtube.com/watch?v=…");
    fireEvent.change(urlInput, {
      target: { value: "https://www.youtube.com/watch?v=example-video" },
    });

    const startButton = screen.getByRole("button", { name: "Start clipping" });
    expect(startButton).not.toBeDisabled();

    // 2. Click Start clipping to enter ProcessingScreen
    await act(async () => {
      fireEvent.click(startButton);
    });

    // Hash should now be updated to #/jobs/job-...
    expect(window.location.hash).toMatch(/^#\/jobs\/job-/);

    // 3. Verify Processing screen appears
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });

    expect(screen.getByText("Getting the video")).toBeInTheDocument();
    expect(screen.getByText("Transcribing speech")).toBeInTheDocument();
    expect(screen.getByText("Finding the best moments")).toBeInTheDocument();
    expect(
      screen.getByText("You can close this tab — the job keeps running on this machine."),
    ).toBeInTheDocument();

    // 4. Advance timers through all stages to completion (~25s)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });

    // 5. Results screen should now render with clips
    expect(
      screen.getByText("The single thing that changed everything"),
    ).toBeInTheDocument();
    expect(screen.getByText("96 Strong")).toBeInTheDocument();
    expect(screen.getByText("Download all (.zip)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New video" })).toBeInTheDocument();

    // 6. Click New video to return to Import screen
    const newVideoBtn = screen.getByRole("button", { name: "New video" });
    await act(async () => {
      fireEvent.click(newVideoBtn);
    });

    expect(window.location.hash).toBe("#/");
    expect(screen.getByText("Paste a link, or drop a file.")).toBeInTheDocument();

    vi.useRealTimers();
  });
});
