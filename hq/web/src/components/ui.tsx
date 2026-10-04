import clsx from "clsx";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { STATE_COLOR } from "../lib/fleet";

export function Panel({ title, right, className, children }: { title?: ReactNode; right?: ReactNode; className?: string; children: ReactNode }) {
  return (
    <section className={clsx("panel p-4", className)}>
      {(title || right) && (
        <header className="mb-3 flex items-center justify-between gap-3">
          <h2 className="panel-title">{title}</h2>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function StateDot({ state, pulse }: { state: string; pulse?: boolean }) {
  const c = STATE_COLOR[state] ?? STATE_COLOR.idle;
  return (
    <span className="relative inline-flex h-2.5 w-2.5">
      {pulse && <span className="absolute inset-0 rounded-full animate-pulseRing" style={{ background: c }} />}
      <span className="relative inline-flex h-2.5 w-2.5 rounded-full" style={{ background: c, boxShadow: `0 0 10px ${c}` }} />
    </span>
  );
}

export function Chip({ color = "#29d9f5", children }: { color?: string; children: ReactNode }) {
  return (
    <span className="chip" style={{ color, borderColor: `${color}66`, background: `${color}12` }}>
      {children}
    </span>
  );
}

// Numbers count up when they appear, the way a readout settles.
export function useCountUp(target: number, ms = 700): number {
  const [v, setV] = useState(0);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { setV(target); return; }
    const t0 = performance.now();
    let raf = 0;
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / ms);
      setV(Math.round(target * (1 - Math.pow(1 - k, 3))));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return v;
}

function Counter({ n }: { n: number }) {
  return <>{useCountUp(n).toLocaleString()}</>;
}

export function Stat({ label, value, color = "#29d9f5", hint, max }: { label: string; value: ReactNode; color?: string; hint?: string; max?: number }) {
  const num = typeof value === "number" ? value : null;
  return (
    <div className="panel px-4 py-3" title={hint}>
      <div className="hud-label">{label}</div>
      <div className="mt-1 font-display text-2xl tabular-nums" style={{ color, textShadow: `0 0 14px ${color}88` }}>
        {num !== null ? <Counter n={num} /> : value}
      </div>
      {num !== null && max ? <div className="bar mt-2" style={{ color }}><i style={{ width: `${Math.min(100, (num / max) * 100)}%` }} /></div> : null}
    </div>
  );
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="prose-hq">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: (p) => <a {...p} target="_blank" rel="noreferrer" /> }}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-10 text-center font-mono text-xs uppercase tracking-widest text-dim">{children}</div>;
}

export function Loading() {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="font-display text-xs tracking-[.4em] text-cyan animate-flicker">LINKING…</div>
    </div>
  );
}

// ---- drawer (slide-in glass panel)

export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true">
      <button aria-label="Close" className="absolute inset-0 bg-void/70 backdrop-blur-sm" onClick={onClose} />
      <aside className="panel relative m-3 flex w-full max-w-2xl flex-col overflow-hidden">
        <header className="flex items-center justify-between border-b border-line px-5 py-3">
          <div className="font-display text-sm tracking-widest text-cyan">{title}</div>
          <button className="btn btn-ghost" onClick={onClose}>Close</button>
        </header>
        <div className="scroll-thin flex-1 overflow-y-auto p-5">{children}</div>
      </aside>
    </div>
  );
}

// ---- confirm + toast, built into the page (the browser's confirm() is not used)

interface ConfirmState { text: string; danger?: boolean; resolve: (ok: boolean) => void }
interface Toast { id: number; text: string; kind: "ok" | "err" }
interface Ctx { confirm: (text: string, danger?: boolean) => Promise<boolean>; toast: (text: string, kind?: "ok" | "err") => void }
const UiCtx = createContext<Ctx>({ confirm: async () => false, toast: () => {} });
export const useUi = () => useContext(UiCtx);

export function UiProvider({ children }: { children: ReactNode }) {
  const [c, setC] = useState<ConfirmState | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const confirm = useCallback((text: string, danger?: boolean) => new Promise<boolean>((resolve) => setC({ text, danger, resolve })), []);
  const toast = useCallback((text: string, kind: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);
  const close = (ok: boolean) => { c?.resolve(ok); setC(null); };
  return (
    <UiCtx.Provider value={{ confirm, toast }}>
      {children}
      {c && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-void/75 backdrop-blur-sm p-4" role="alertdialog" aria-modal="true">
          <div className="panel w-full max-w-md p-5">
            <div className="panel-title mb-3">{c.danger ? "Confirm critical action" : "Confirm"}</div>
            <p className="text-sm text-ink/90">{c.text}</p>
            <div className="mt-5 flex justify-end gap-2">
              <button className="btn btn-ghost" onClick={() => close(false)}>Cancel</button>
              <button className={clsx("btn", c.danger ? "btn-alert" : "btn-amber")} onClick={() => close(true)} autoFocus>Confirm</button>
            </div>
          </div>
        </div>
      )}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={clsx("panel px-4 py-2 font-mono text-xs", t.kind === "err" ? "text-alert" : "text-mint")}>{t.text}</div>
        ))}
      </div>
    </UiCtx.Provider>
  );
}
