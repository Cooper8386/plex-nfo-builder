/**
 * In-app confirmation dialog (v0.11.9) — replaces native ``window.confirm`` and
 * ``window.prompt``. Two pieces:
 *
 *  1. ``<ConfirmProvider>`` mounted once at the app root. It owns the modal
 *     element and a queue. The Confirm button is auto-focused so pressing
 *     Enter accepts the dialog, matching the user's request from v0.11.9.
 *
 *  2. ``useConfirm()`` / ``usePrompt()`` hooks return a promise. ``useConfirm``
 *     resolves to ``true`` / ``false``; ``usePrompt`` resolves to the typed
 *     value or ``null`` when cancelled. Replacements for ``window.confirm`` and
 *     ``window.prompt`` respectively.
 *
 * Behaviour:
 * - Confirm button is the default focus target (Enter to accept).
 * - Escape cancels.
 * - Hazard-yellow styling for ``tone === "danger"``, indigo for default.
 * - Backdrop click cancels (matches native confirm dismissal).
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

type Tone = "default" | "danger";

type ConfirmOptions = {
  title?: string;
  message: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: Tone;
};

type PromptOptions = {
  title?: string;
  message?: React.ReactNode;
  defaultValue?: string;
  placeholder?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: Tone;
};

type Pending =
  | {
      mode: "confirm";
      opts: ConfirmOptions;
      resolve: (v: boolean) => void;
    }
  | {
      mode: "prompt";
      opts: PromptOptions;
      resolve: (v: string | null) => void;
    };

type Ctx = {
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
  prompt: (opts: PromptOptions) => Promise<string | null>;
};

const ConfirmCtx = createContext<Ctx | null>(null);

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [draft, setDraft] = useState("");
  const confirmBtnRef = useRef<HTMLButtonElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const confirm = useCallback(
    (opts: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        setPending({ mode: "confirm", opts, resolve });
      }),
    [],
  );

  const promptFn = useCallback(
    (opts: PromptOptions) =>
      new Promise<string | null>((resolve) => {
        setDraft(opts.defaultValue ?? "");
        setPending({ mode: "prompt", opts, resolve });
      }),
    [],
  );

  // Expose globals so non-React code paths (and any stragglers we didn't
  // refactor) can opt into the same UI without prop-drilling. Falls back
  // to the native APIs when the provider isn't mounted.
  useEffect(() => {
    (window as any).__pnbConfirm = confirm;
    (window as any).__pnbPrompt = promptFn;
    return () => {
      delete (window as any).__pnbConfirm;
      delete (window as any).__pnbPrompt;
    };
  }, [confirm, promptFn]);

  // Auto-focus the Confirm button (or the input for prompts) so Enter
  // accepts the dialog without the user having to tab.
  useEffect(() => {
    if (!pending) return;
    const t = setTimeout(() => {
      if (pending.mode === "prompt") {
        inputRef.current?.focus();
        inputRef.current?.select();
      } else {
        confirmBtnRef.current?.focus();
      }
    }, 0);
    return () => clearTimeout(t);
  }, [pending]);

  // Esc cancels the active dialog. We use a window listener instead of the
  // input/button onKeyDown so it works even when neither has focus.
  useEffect(() => {
    if (!pending) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        cancel();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending]);

  function cancel() {
    if (!pending) return;
    if (pending.mode === "prompt") pending.resolve(null);
    else pending.resolve(false);
    setPending(null);
    setDraft("");
  }

  function accept() {
    if (!pending) return;
    if (pending.mode === "prompt") pending.resolve(draft);
    else pending.resolve(true);
    setPending(null);
    setDraft("");
  }

  const value = useMemo<Ctx>(
    () => ({ confirm, prompt: promptFn }),
    [confirm, promptFn],
  );

  const tone: Tone = pending?.opts.tone ?? "default";
  const confirmCls =
    tone === "danger"
      ? "bg-amber-400 hover:bg-amber-300 text-black border-2 border-amber-500"
      : "bg-indigo-600 hover:bg-indigo-500 text-white border-2 border-indigo-700";

  return (
    <ConfirmCtx.Provider value={value}>
      {children}
      {pending && (
        <div
          className="fixed inset-0 z-[1000] flex items-center justify-center p-4 bg-black/60"
          onClick={(e) => {
            // Backdrop click cancels — matches native confirm dismissal.
            if (e.target === e.currentTarget) cancel();
          }}
          role="presentation"
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label={pending.opts.title || "Confirm"}
            className="w-full max-w-md bg-slate-900 border border-slate-700 rounded-lg shadow-xl"
          >
            {pending.opts.title && (
              <div className="px-5 pt-4 pb-2 text-base font-semibold text-slate-100">
                {pending.opts.title}
              </div>
            )}
            <div className="px-5 pb-3 text-sm text-slate-200 whitespace-pre-line">
              {pending.mode === "confirm"
                ? pending.opts.message
                : pending.opts.message}
            </div>
            {pending.mode === "prompt" && (
              <div className="px-5 pb-3">
                <input
                  ref={inputRef}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      accept();
                    }
                  }}
                  placeholder={pending.opts.placeholder}
                  className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-sm text-slate-100"
                />
              </div>
            )}
            <div className="px-5 pb-4 pt-2 flex items-center justify-end gap-2">
              <button
                onClick={cancel}
                className="px-3 py-1.5 rounded text-sm bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700"
              >
                {pending.opts.cancelLabel ?? "Cancel"}
              </button>
              <button
                ref={confirmBtnRef}
                onClick={accept}
                onKeyDown={(e) => {
                  // Enter on the focused button accepts; the native button
                  // already handles this, but be explicit so Space also works.
                  if (e.key === "Enter") {
                    e.preventDefault();
                    accept();
                  }
                }}
                className={`px-3 py-1.5 rounded text-sm font-semibold ${confirmCls}`}
              >
                {pending.opts.confirmLabel ?? "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmCtx.Provider>
  );
}

export function useConfirm() {
  const ctx = useContext(ConfirmCtx);
  if (!ctx) {
    // Fall back to the global the provider installs, then to native.
    return (opts: ConfirmOptions) => {
      const g = (window as any).__pnbConfirm as
        | ((o: ConfirmOptions) => Promise<boolean>)
        | undefined;
      if (g) return g(opts);
      // eslint-disable-next-line no-alert
      return Promise.resolve(window.confirm(asText(opts.message)));
    };
  }
  return ctx.confirm;
}

export function usePrompt() {
  const ctx = useContext(ConfirmCtx);
  if (!ctx) {
    return (opts: PromptOptions) => {
      const g = (window as any).__pnbPrompt as
        | ((o: PromptOptions) => Promise<string | null>)
        | undefined;
      if (g) return g(opts);
      // eslint-disable-next-line no-alert
      return Promise.resolve(
        window.prompt(asText(opts.message ?? ""), opts.defaultValue ?? ""),
      );
    };
  }
  return ctx.prompt;
}

function asText(node: React.ReactNode): string {
  if (node == null || node === false) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(asText).join("");
  if (typeof node === "object" && "props" in (node as any)) {
    return asText((node as any).props.children);
  }
  return "";
}
