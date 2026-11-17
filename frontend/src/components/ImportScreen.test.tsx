import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ImportScreen } from "./ImportScreen";
import { ToastProvider } from "./ui/toast";
import { MockApiClient } from "@/api/mockClient";

describe("ImportScreen", () => {
  it("validates URL and displays error on invalid format", async () => {
    const client = new MockApiClient();
    const presets = await client.listCaptionStyles();
    const onSubmit = vi.fn();
    const onUpload = vi.fn();

    render(
      <ToastProvider>
        <ImportScreen
          captionStyles={presets}
          onSubmit={onSubmit}
          onUploadFile={onUpload}
        />
      </ToastProvider>,
    );

    const input = screen.getByPlaceholderText("https://youtube.com/watch?v=…");
    const startButton = screen.getByRole("button", { name: "Start clipping" });

    expect(startButton).toBeDisabled();

    // Type invalid url
    fireEvent.change(input, { target: { value: "not-a-valid-url" } });
    fireEvent.blur(input);

    expect(
      screen.getByText("That doesn't look like a link. Use an http(s) URL or drop a file."),
    ).toBeInTheDocument();
    expect(startButton).toBeDisabled();

    // Type valid url
    fireEvent.change(input, { target: { value: "https://www.youtube.com/watch?v=dQw4w9WgXcQ" } });
    fireEvent.blur(input);

    expect(
      screen.queryByText("That doesn't look like a link. Use an http(s) URL or drop a file."),
    ).not.toBeInTheDocument();
    expect(startButton).not.toBeDisabled();

    // Submit
    fireEvent.click(startButton);
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        source: {
          type: "url",
          value: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        },
      }),
    );
  });

  it("handles file drop and displays file chip", async () => {
    const client = new MockApiClient();
    const presets = await client.listCaptionStyles();
    const onSubmit = vi.fn();
    const onUpload = vi.fn().mockResolvedValue({
      upload_id: "upload-abc-123",
      filename: "interview.mp4",
      size_bytes: 50 * 1024 * 1024,
    });

    render(
      <ToastProvider>
        <ImportScreen
          captionStyles={presets}
          onSubmit={onSubmit}
          onUploadFile={onUpload}
        />
      </ToastProvider>,
    );

    const dropZone = screen.getByRole("button", {
      name: /Drop a video here/i,
    });
    expect(dropZone).toBeInTheDocument();

    const file = new File(["dummy content"], "interview.mp4", { type: "video/mp4" });
    fireEvent.drop(dropZone, {
      dataTransfer: {
        files: [file],
      },
    });

    await waitFor(() => {
      // onUpload is now called with (file, progressCallback?) — check the first arg
      expect(onUpload).toHaveBeenCalledWith(file, expect.any(Function));
    });

    await waitFor(() => {
      expect(screen.getByText("interview.mp4")).toBeInTheDocument();
    });

    const startButton = screen.getByRole("button", { name: "Start clipping" });
    expect(startButton).not.toBeDisabled();

    fireEvent.click(startButton);
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        source: {
          type: "upload",
          value: "upload-abc-123",
        },
      }),
    );
  });
});
