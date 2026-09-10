import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Modal from "./Modal";
import {
  ConfirmContext,
  type ConfirmOptions,
  type PromptOptions,
} from "./confirm";

type Pending = (
  | { mode: "confirm"; opts: ConfirmOptions; resolve: (value: boolean) => void }
  | {
      mode: "prompt";
      opts: PromptOptions;
      resolve: (value: string | null) => void;
    }
) & { returnFocus?: HTMLElement | null };

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const queue = useRef<Pending[]>([]);
  const lastFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const remember = (event: FocusEvent) => {
      if (
        event.target instanceof HTMLElement &&
        !event.target.closest("dialog")
      )
        lastFocus.current = event.target;
    };
    document.addEventListener("focusin", remember);
    return () => document.removeEventListener("focusin", remember);
  }, []);
  const [pending, setPending] = useState<Pending>();
  const enqueue = useCallback((request: Pending) => {
    request.returnFocus =
      document.activeElement instanceof HTMLElement &&
      document.activeElement !== document.body
        ? document.activeElement
        : lastFocus.current;
    queue.current.push(request);
    setPending(queue.current[0]);
  }, []);
  const confirm = useCallback(
    (opts: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        enqueue({ mode: "confirm", opts, resolve });
      }),
    [enqueue],
  );
  const prompt = useCallback(
    (opts: PromptOptions) =>
      new Promise<string | null>((resolve) => {
        enqueue({ mode: "prompt", opts, resolve });
      }),
    [enqueue],
  );
  const finish = (value: string | boolean | null) => {
    const request = queue.current.shift();
    if (request?.mode === "confirm") request.resolve(value === true);
    else if (request) request.resolve(typeof value === "string" ? value : null);
    setPending(queue.current[0]);
  };
  useEffect(
    () => () => {
      queue.current.splice(0).forEach((request) => {
        if (request.mode === "confirm") request.resolve(false);
        else request.resolve(null);
      });
    },
    [],
  );
  const value = useMemo(() => ({ confirm, prompt }), [confirm, prompt]);
  return (
    <ConfirmContext.Provider value={value}>
      {children}
      {pending && <Confirmation request={pending} finish={finish} />}
    </ConfirmContext.Provider>
  );
}

function Confirmation({
  request,
  finish,
}: {
  request: Pending;
  finish: (value: string | boolean | null) => void;
}) {
  const [draft, setDraft] = useState("");
  const cancelRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    setDraft(
      request.mode === "prompt" ? (request.opts.defaultValue ?? "") : "",
    );
    if (request.mode === "prompt") inputRef.current?.focus();
    else cancelRef.current?.focus();
  }, [request]);
  const { opts } = request;
  return (
    <Modal
      title={opts.title ?? "Confirm action"}
      onClose={() => finish(null)}
      returnFocus={request.returnFocus}
      className="confirm-modal"
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          finish(request.mode === "prompt" ? draft : true);
        }}
      >
        {opts.tone === "danger" && (
          <div className="danger-label">Review scope before continuing</div>
        )}
        <div className="modal-body whitespace-pre-line text-sm text-slate-300">
          {opts.message}
        </div>
        {request.mode === "prompt" && (
          <label className="block px-5 pb-4 text-sm">
            <span className="sr-only">{opts.title ?? "Value"}</span>
            <input
              ref={inputRef}
              className="field w-full"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={request.opts.placeholder}
            />
          </label>
        )}
        <div className="modal-footer">
          <button
            ref={cancelRef}
            type="button"
            className="btn"
            onClick={() => finish(null)}
          >
            {opts.cancelLabel ?? "Cancel"}
          </button>
          <button
            type="submit"
            className={`btn ${opts.tone === "danger" ? "btn-danger" : "btn-primary"}`}
          >
            {opts.confirmLabel ?? "Confirm"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
