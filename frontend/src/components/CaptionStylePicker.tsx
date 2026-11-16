import { useState, useRef, type CSSProperties } from "react";
import { Check } from "lucide-react";
import type { CaptionStyle } from "@/api/client";
import { cn } from "@/lib/utils";

interface CaptionStylePickerProps {
  styles: CaptionStyle[];
  selectedId: string;
  onSelect: (id: string) => void;
  className?: string;
  compact?: boolean; // For Import screen snap-scroll row
}

export function CaptionStylePreview({
  style,
  isHovered = false,
}: {
  style: CaptionStyle;
  isHovered?: boolean;
}) {
  const isUppercase = style.uppercase;
  const isBold = style.bold;
  const fontFamily = style.font_family;
  const outlineWidth = style.outline_width || 0;
  const outlineColor = style.outline_color || "transparent";

  // Build text shadow for stroke / shadow
  const textShadowParts: string[] = [];
  if (outlineWidth > 0 && outlineColor !== "transparent") {
    // 8-direction shadow approximation for crisp outline in HTML
    const w = Math.min(Math.max(outlineWidth / 3, 1), 3);
    textShadowParts.push(
      `-${w}px -${w}px 0 ${outlineColor}`,
      `${w}px -${w}px 0 ${outlineColor}`,
      `-${w}px ${w}px 0 ${outlineColor}`,
      `${w}px ${w}px 0 ${outlineColor}`,
      `0px -${w}px 0 ${outlineColor}`,
      `0px ${w}px 0 ${outlineColor}`,
      `-${w}px 0px 0 ${outlineColor}`,
      `${w}px 0px 0 ${outlineColor}`,
    );
  }
  if (style.shadow_color && style.shadow_offset) {
    textShadowParts.push(`0px ${style.shadow_offset / 2}px 4px ${style.shadow_color}`);
  }

  const containerStyle: CSSProperties = {
    fontFamily: `"${fontFamily}", Inter, sans-serif`,
    letterSpacing: `${style.letter_spacing}px`,
    backgroundColor: style.background?.color || "transparent",
    borderRadius: style.background?.radius ? `${style.background.radius / 4}px` : "6px",
    padding: style.background?.padding ? `${style.background.padding / 6}px` : "6px 8px",
  };

  const isBoxMode = style.highlight_mode === "word_box";
  const isNoneMode = style.highlight_mode === "none";
  const isKaraoke = style.highlight_mode === "karaoke_fill";

  return (
    <div
      className="relative flex h-24 w-full items-center justify-center overflow-hidden rounded-md bg-[#07080a] p-2 select-none"
      style={{
        backgroundImage: "radial-gradient(ellipse at center, #191b20 0%, #08090b 100%)",
      }}
    >
      <div
        className={cn(
          "inline-flex flex-wrap items-center justify-center gap-1.5 text-center text-xs transition-transform duration-200",
          isUppercase && "uppercase",
          isBold ? "font-bold" : "font-normal",
        )}
        style={containerStyle}
      >
        <span
          style={{
            color: style.text_color,
            textShadow: textShadowParts.join(", "),
          }}
        >
          this is the part that
        </span>

        <span
          className={cn(
            "relative inline-block transition-transform duration-200",
            style.animation === "pop" && isHovered && "scale-110",
            style.animation === "bounce" && isHovered && "-translate-y-0.5",
            style.animation === "slide_up" && isHovered && "-translate-y-0.5",
            style.animation === "fade" && isHovered && "opacity-90",
            isBoxMode && "px-1.5 py-0.5 rounded font-bold",
          )}
          style={{
            color: isNoneMode
              ? style.text_color
              : isBoxMode
                ? "#FFFFFF"
                : style.highlight_color,
            backgroundColor: isBoxMode ? style.highlight_color : "transparent",
            textShadow: isBoxMode ? "none" : textShadowParts.join(", "),
            boxShadow:
              isKaraoke && isHovered
                ? `0 0 10px ${style.highlight_color}`
                : undefined,
          }}
        >
          hooks
        </span>

        <span
          style={{
            color: style.text_color,
            textShadow: textShadowParts.join(", "),
          }}
        >
          them
        </span>
      </div>
    </div>
  );
}

export function CaptionStylePicker({
  styles,
  selectedId,
  onSelect,
  className,
  compact = false,
}: CaptionStylePickerProps) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleKeyDown = (e: React.KeyboardEvent, index: number) => {
    let nextIndex = index;
    const total = styles.length;

    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      nextIndex = (index + 1) % total;
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      nextIndex = (index - 1 + total) % total;
    } else if (e.key === "Home") {
      e.preventDefault();
      nextIndex = 0;
    } else if (e.key === "End") {
      e.preventDefault();
      nextIndex = total - 1;
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelect(styles[index].id);
      return;
    } else {
      return;
    }

    const nextStyle = styles[nextIndex];
    if (nextStyle) {
      onSelect(nextStyle.id);
      const buttons = containerRef.current?.querySelectorAll<HTMLButtonElement>('[role="radio"]');
      buttons?.[nextIndex]?.focus();
    }
  };

  return (
    <div
      ref={containerRef}
      role="radiogroup"
      aria-label="Caption styles"
      className={cn(
        compact
          ? "flex gap-3 overflow-x-auto pb-2 pt-1 snap-x no-scrollbar"
          : "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3",
        className,
      )}
    >
      {styles.map((style, index) => {
        const isSelected = style.id === selectedId;
        const isHovered = hoveredId === style.id;

        return (
          <button
            key={style.id}
            type="button"
            role="radio"
            aria-checked={isSelected}
            tabIndex={isSelected ? 0 : -1}
            onClick={() => onSelect(style.id)}
            onKeyDown={(e) => handleKeyDown(e, index)}
            onMouseEnter={() => setHoveredId(style.id)}
            onMouseLeave={() => setHoveredId(null)}
            className={cn(
              "group relative flex flex-col rounded-xl border p-2 text-left transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
              compact ? "min-w-[200px] w-[220px] shrink-0 snap-start" : "w-full",
              isSelected
                ? "border-primary ring-2 ring-primary bg-card/90"
                : "border-border bg-card/50 hover:border-foreground/30 hover:bg-card",
            )}
          >
            {/* Visual preview */}
            <CaptionStylePreview style={style} isHovered={isHovered || isSelected} />

            {/* Info header */}
            <div className="mt-2.5 flex items-center justify-between px-1">
              <span
                className={cn(
                  "text-sm font-medium transition-colors",
                  isSelected ? "font-semibold text-foreground" : "text-foreground/90",
                )}
              >
                {style.name}
              </span>
              {isSelected && (
                <span
                  className="flex h-5 w-5 items-center justify-center rounded-full bg-primary text-primary-foreground"
                  aria-hidden="true"
                >
                  <Check className="h-3 w-3 stroke-[3]" />
                </span>
              )}
            </div>

            {/* Description */}
            <p className="mt-1 line-clamp-2 px-1 text-xs text-muted-foreground">
              {style.description}
            </p>
          </button>
        );
      })}
    </div>
  );
}
