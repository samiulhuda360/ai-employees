import clsx from "clsx";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { api, clock, due, type Overview } from "../lib/api";
import { LOOK } from "../lib/fleet";
import Claudia from "./Claudia";
import ClaudiaAvatar from "./ClaudiaAvatar";
import { useUi } from "./ui";

const NAV: { to: string; label: string; icon: ReactNode }[] = [
  { to: "/", label: "Deck", icon: <path d="M12 3 21 8v8l-9 5-9-5V8z M12 8v8 M7.5 10.5 12 13l4.5-2.5" /> },
  { to: "/agents", label: "Agents", icon: <path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-7 9a7 7 0 0 1 14 0" /> },
  { to: "/ideas", label: "Ideas", icon: <path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9V16h7v-2.1A6 6 0 0 0 12 3z" /> },
  { to: "/build", label: "Build", icon: <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.3-.7-.7-2.3z" /> },
  { to: "/write", label: "Write", icon: <path d="M4 20h4L19 9l-4-4L4 16v4z M13.5 6.5l4 4" /> },
  { to: "/prospects", label: "Prospects", icon: <path d="M12 21s-7-6-7-11a7 7 0 0 1 14 0c0 5-7 11-7 11zm0-9a2 2 0 1 0 0-4 2 2 0 0 0 0 4z" /> },
  { to: "/customers", label: "Customers", icon: <path d="M3 21V10m6 11V4m6 17v-8m6 8V7" /> },
  { to: "/me", label: "Me", icon: <path d="M4 5h16v14H4z M8 9h8 M8 13h5" /> },
  { to: "/log", label: "Log", icon: <path d="M5 4h14v16H5z M8 8h8 M8 12h8 M8 16h5" /> },
];

function LocalClock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const time = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const day = now.toLocaleDateString(undefined, { weekday: "short", day: "2-digit", month: "short" });
  return (
    <div className="text-right leading-tight">
      <div className="font-mono text-sm text-ink tabular-nums">{time}</div>
      <div className="hud-label">{day} · local</div>
    </div>
  );
}

// The status line under the title: the worst thing going on right now, or nominal.
function status(o: Overview | null): { text: string; color: string } {
  if (!o) return { text: "linking…", color: "#7894b3" };
  if (o.fleet_paused) return { text: "emergency stop engaged", color: "#ff4d6d" };
  const failed = o.agents.filter((a) => a.state === "failed").length;
  const late = o.agents.filter((a) => a.state === "late").length;
  if (failed) return { text: `${failed} agent${failed > 1 ? "s" : ""} failed`, color: "#ff4d6d" };
  if (late) return { text: `${late} agent${late > 1 ? "s" : ""} running late`, color: "#f7b538" };
  if (o.fallback_24h) return { text: `backup model used ${o.fallback_24h}x in 24h`, color: "#f7b538" };
  if (o.quiet_hours) return { text: "quiet hours · messages held", color: "#ff8a65" };
  return { text: "all systems nominal", color: "#3ee6a8" };
}

// One scrolling line of live facts: what is due next, what needs you, what just ran.
function Ticker({ o }: { o: Overview | null }) {
  if (!o) return null;
  const next = o.agents.filter((a) => a.next_run_at && a.state !== "paused")
    .sort((a, b) => (a.next_run_at! < b.next_run_at! ? -1 : 1))[0];
  const items: { k: string; text: string; color?: string }[] = [];
  if (o.priority.p1) items.push({ k: "p1", text: `${o.priority.p1} DO FIRST · ${o.priority.p2} WORTH DOING`, color: "#ff4d6d" });
  if (next) items.push({ k: "next", text: `NEXT: ${LOOK[next.id]?.name ?? next.name} ${due(next.next_run_at)}` });
  if (o.customers) items.push({ k: "cust", text: `${o.customers.paying} PAYING · ${o.customers.at_risk} AT RISK`, color: "#f7b538" });
  if (o.fallback_24h) items.push({ k: "fb", text: `BACKUP MODEL x${o.fallback_24h}`, color: "#f7b538" });
  for (const r of o.timeline.slice(0, 8)) {
    items.push({ k: `t${r.started_at}${r.job_name}`, text: `${clock(r.started_at)} ${(LOOK[r.agent]?.name ?? r.agent).toUpperCase()} · ${r.job_name}`,
                 color: r.status === "failed" ? "#ff4d6d" : undefined });
  }
  if (!items.length) return null;
  const line = (suffix: string) => items.map((i) => (
    <span key={i.k + suffix} className="mx-6 font-mono text-[10px] tracking-[.18em]" style={{ color: i.color ?? "#7894b3" }}>
      <span className="mr-2 text-cyan/60">◆</span>{i.text}
    </span>
  ));
  return (
    <div className="ticker relative z-20 border-b border-line/60 bg-void/50 py-1" aria-hidden>
      <div className="ticker-track">{line("a")}{line("b")}</div>
    </div>
  );
}

export default function Shell({ children, overview, onFleetChange }: { children: ReactNode; overview: Overview | null; onFleetChange: () => void }) {
  const { confirm, toast } = useUi();
  const fleetPaused = overview?.fleet_paused ?? false;
  const st = status(overview);
  const loc = useLocation();
  const toggleFleet = async () => {
    const pausing = !fleetPaused;
    const ok = await confirm(
      pausing
        ? "Emergency stop: no agent starts new work until you resume. Anything already running finishes."
        : "Resume the fleet: scheduled jobs start again from their next run time.",
      pausing,
    );
    if (!ok) return;
    try {
      await api.post(`/fleet/${pausing ? "pause" : "resume"}`);
      toast(pausing ? "Fleet stopped" : "Fleet resumed");
      onFleetChange();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };
  const logout = async () => {
    await api.post("/logout").catch(() => {});
    window.dispatchEvent(new Event("hq:logout"));
  };

  return (
    <div className="flex h-full flex-col">
      <header className="relative z-20 flex items-center justify-between gap-4 border-b border-line/80 bg-void/70 px-4 py-2 backdrop-blur">
        <div className="flex items-center gap-3">
          <svg viewBox="0 0 64 64" className="h-7 w-7" aria-hidden>
            <polygon points="32,4 58,18 58,46 32,60 6,46 6,18" fill="none" stroke="#29d9f5" strokeWidth="4" />
            <circle cx="32" cy="32" r="8" fill="#f7b538" />
          </svg>
          <div>
            <div className="font-display text-sm tracking-[.35em] text-ink glow-text">HERMES HQ</div>
            <div className="hud-label">Fleet command · <span style={{ color: st.color }}>{st.text}</span>
              <span className="ml-2 inline-block h-1.5 w-1.5 rounded-full align-middle animate-blink" style={{ background: st.color, boxShadow: `0 0 8px ${st.color}` }} /></div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Claudia />
          <button className={clsx("btn", fleetPaused ? "btn-amber" : "btn-alert")} onClick={toggleFleet}>
            {fleetPaused ? "Resume fleet" : "Emergency stop"}
          </button>
          <LocalClock />
          <button className="btn btn-ghost" onClick={logout} title="Log out">Exit</button>
        </div>
      </header>
      <Ticker o={overview} />
      <div className="flex min-h-0 flex-1">
        <nav className="relative z-20 flex w-[76px] shrink-0 flex-col items-center gap-1 border-r border-line/80 bg-void/60 py-3 backdrop-blur" aria-label="Main">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}
              className={({ isActive }) => clsx("group relative flex w-full flex-col items-center gap-1 py-2 transition",
                isActive ? "nav-active text-cyan" : "text-dim hover:text-ink")}>
              {({ isActive }) => (
                <>
                  <svg viewBox="0 0 24 24" className={clsx("h-5 w-5", isActive && "drop-shadow-[0_0_6px_#29d9f5]")} fill="none"
                       stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{n.icon}</svg>
                  <span className="font-display text-[8px] tracking-[.18em] uppercase">{n.label}</span>
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <main className="relative min-w-0 flex-1 overflow-hidden">
          <div key={loc.pathname} className="h-full animate-wipe">{children}</div>
        </main>
        <ClaudiaAvatar />
      </div>
    </div>
  );
}
