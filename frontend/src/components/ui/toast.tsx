import { useState, useCallback, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { ToastContext } from "./use-toast";

interface Toast {
  id: string;
  message: string;
  variant?: "default" | "warning" | "destructive";
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const toast = useCallback(
    (message: string, variant: "default" | "warning" | "destructive" = "default") => {
      const id = `${Date.now()}-${Math.random().toString(36).substring(2, 6)}`;
      setToasts((prev) => [...prev, { id, message, variant }]);
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, 3000);
    },
    [],
  );

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div
        className="fixed bottom-6 right-6 z-50 flex flex-col gap-2 max-w-sm"
        aria-live="polite"
        role="region"
        aria-label="Notifications"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cn(
              "rounded-lg border px-4 py-3 text-sm shadow-lg transition-all animate-in slide-in-from-bottom-2",
              t.variant === "default" && "border-border bg-card text-foreground",
              t.variant === "warning" && "border-warning/50 bg-card text-warning",
              t.variant === "destructive" && "border-destructive/50 bg-card text-destructive",
            )}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
