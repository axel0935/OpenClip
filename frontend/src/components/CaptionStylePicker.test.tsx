import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { CaptionStylePicker } from "./CaptionStylePicker";
import { MockApiClient } from "@/api/mockClient";

describe("CaptionStylePicker", () => {
  it("renders all 8 presets with their titles and sample text", async () => {
    const client = new MockApiClient();
    const presets = await client.listCaptionStyles();
    const onSelect = vi.fn();

    render(
      <CaptionStylePicker
        styles={presets}
        selectedId="clean-pop"
        onSelect={onSelect}
      />,
    );

    expect(presets).toHaveLength(8);
    for (const preset of presets) {
      expect(screen.getByText(preset.name)).toBeInTheDocument();
    }

    // Every tile should render sample text with highlighted "hooks"
    const hooksElements = screen.getAllByText("hooks");
    expect(hooksElements).toHaveLength(8);
  });

  it("handles keyboard navigation between styles using arrow keys", async () => {
    const client = new MockApiClient();
    const presets = await client.listCaptionStyles();
    const onSelect = vi.fn();

    render(
      <CaptionStylePicker
        styles={presets}
        selectedId="clean-pop"
        onSelect={onSelect}
      />,
    );

    const radioGroup = screen.getByRole("radiogroup");
    const radios = screen.getAllByRole("radio");

    expect(radioGroup).toBeInTheDocument();
    expect(radios).toHaveLength(8);

    const cleanPopIndex = presets.findIndex((p) => p.id === "clean-pop");
    const otherIndex = cleanPopIndex === 0 ? 1 : 0;

    // Initial selected tile has tabIndex 0
    expect(radios[cleanPopIndex]).toHaveAttribute("tabindex", "0");
    expect(radios[otherIndex]).toHaveAttribute("tabindex", "-1");

    // Press ArrowRight to select the next preset
    fireEvent.keyDown(radios[cleanPopIndex], { key: "ArrowRight" });
    const expectedNextIndex = (cleanPopIndex + 1) % presets.length;
    expect(onSelect).toHaveBeenCalledWith(presets[expectedNextIndex].id);

    // Press End to jump to last preset
    fireEvent.keyDown(radios[cleanPopIndex], { key: "End" });
    expect(onSelect).toHaveBeenCalledWith(presets[presets.length - 1].id);

    // Press Home to jump to first preset
    fireEvent.keyDown(radios[presets.length - 1], { key: "Home" });
    expect(onSelect).toHaveBeenCalledWith(presets[0].id);
  });

  it("selects preset on click", async () => {
    const client = new MockApiClient();
    const presets = await client.listCaptionStyles();
    const onSelect = vi.fn();

    render(
      <CaptionStylePicker
        styles={presets}
        selectedId="clean-pop"
        onSelect={onSelect}
      />,
    );

    const boldImpact = screen.getByText("Bold Impact");
    fireEvent.click(boldImpact);

    expect(onSelect).toHaveBeenCalledWith("bold-impact");
  });
});
