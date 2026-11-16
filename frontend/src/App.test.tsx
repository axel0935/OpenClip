import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import App from "./App";

test("shows the OpenClip shell and local API status", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: "ok", version: "0.1.0" }) }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
  expect(screen.getByRole("heading", { name: "OpenClip" })).toBeInTheDocument();
  expect(await screen.findByText("API ok · v0.1.0")).toBeInTheDocument();
});
