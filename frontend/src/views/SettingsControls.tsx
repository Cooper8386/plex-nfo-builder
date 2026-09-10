import { useId } from "react";

export function PaneHeader({
  title,
  subtitle,
}: {
  title: string;
  subtitle?: string;
}) {
  return (
    <div className="mb-5">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {subtitle && <p className="text-sm text-slate-500 mt-1">{subtitle}</p>}
    </div>
  );
}

export function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-slate-800 bg-slate-900/40 px-3 py-2.5">
      {children}
    </div>
  );
}

export function CardLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-0.5">
      {children}
    </div>
  );
}

export function SubHeader({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-2 mt-1">
      {children}
    </h3>
  );
}

export function Divider() {
  return <hr className="my-5 border-slate-800" />;
}

export function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  const id = useId();
  return (
    <div className="grid grid-cols-1 xl:grid-cols-[13rem_minmax(0,1fr)] items-start gap-2 xl:gap-5 py-3 border-b border-slate-800/70 last:border-0">
      <label id={id} className="text-sm text-slate-300 pt-1">
        {label}
      </label>
      <div
        className="min-w-0 [&_input:not([type=checkbox])]:max-w-full [&_select]:max-w-full"
        ref={(element) => {
          // Nested control groups (language pickers, checkboxes + hints) share the field label.
          element
            ?.querySelectorAll("input, select, textarea")
            .forEach((control) => {
              if (
                !control.hasAttribute("aria-label") &&
                !control.hasAttribute("aria-labelledby")
              )
                control.setAttribute("aria-labelledby", id);
            });
        }}
      >
        {children}
      </div>
    </div>
  );
}
