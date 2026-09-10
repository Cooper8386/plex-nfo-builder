import { createContext, useContext } from "react";

export type ConfirmOptions = {
  title?: string;
  message: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "default" | "danger";
};
export type PromptOptions = Omit<ConfirmOptions, "message"> & {
  message?: React.ReactNode;
  defaultValue?: string;
  placeholder?: string;
};
export const ConfirmContext = createContext<{
  confirm: (options: ConfirmOptions) => Promise<boolean>;
  prompt: (options: PromptOptions) => Promise<string | null>;
} | null>(null);

export function useConfirm() {
  const context = useContext(ConfirmContext);
  if (!context) throw new Error("useConfirm requires ConfirmProvider");
  return context.confirm;
}
export function usePrompt() {
  const context = useContext(ConfirmContext);
  if (!context) throw new Error("usePrompt requires ConfirmProvider");
  return context.prompt;
}
