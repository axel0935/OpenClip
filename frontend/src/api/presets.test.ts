import { describe, it, expect } from "vitest";

describe("presets loader", () => {
  it("loads 8 caption presets from ../../../presets/captions", () => {
    const modules = import.meta.glob("../../../presets/captions/*.json", { eager: true });
    const keys = Object.keys(modules);
    expect(keys.length).toBe(8);
  });
});
