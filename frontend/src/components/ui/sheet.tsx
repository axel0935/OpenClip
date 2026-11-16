import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export interface SheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
  side?: "right" | "bottom" | "left";
  title?: string;
  description?: string;
  className?: string;
}

export function Sheet({
  open,
  onOpenChange,
  children,
  side = "right",
  title,
  description,
  className,
}: SheetProps) {
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && open) {
        onOpenChange(false);
      }
    }
    if (open) {
      document.body.style.overflow = "hidden";
      window.addEventListener("keydown", handleKeyDown);
    }
    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, onOpenChange]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex" role="dialog" aria-modal="true" aria-label={title}>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
        onClick={() => onOpenChange(false)}
        aria-hidden="true"
      />

      {/* Sheet Content */}
      <div
        className={cn(
          "relative z-50 flex flex-col bg-card border-border p-6 shadow-2xl transition-transform duration-200 ease-out",
          side === "right" &&
            "ml-auto h-full w-full max-w-md border-l animate-in slide-in-from-right",
          side === "bottom" &&
            "mt-auto h-[85vh] w-full rounded-t-2xl border-t animate-in slide-in-from-bottom",
          side === "left" &&
            "mr-auto h-full w-full max-w-md border-r animate-in slide-in-from-left",
          className,
        )}
      >
        <div className="flex items-center justify-between pb-4">
          <div>
            {title && <h2 className="text-lg font-semibold text-foreground">{title}</h2>}
            {description && (
              <p className="text-sm text-muted-foreground mt-0.5">{description}</p>
            )}
          </div>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}
