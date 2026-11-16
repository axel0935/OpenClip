import { createContext, useContext } from "react";

export interface ToastContextType {
  toast: (message: string, variant?: "default" | "warning" | "destructive") => void;
}

export const ToastContext = createContext<ToastContextType>({
  toast: () => {},
});

export function useToast() {
  return useContext(ToastContext);
}
