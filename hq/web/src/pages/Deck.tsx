import { lazy, Suspense, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, clock, due, type Overview } from "../lib/api";
import { LOOK, STATE_COLOR, TYPE_LABEL } from "../lib/fleet";
import { canSpeak, speak, stopSpeaking } from "../lib/voice";
import { Chip, Loading, StateDot, useUi } from "../components/ui";

const DeckScene = lazy(() => import("../scene/DeckScene"));

// What Claudia says when you press "Brief me". Built only from live data.
function briefing(o: Overview): string {
  const failed = o.agents.filter((a) => a.state === "failed").map((a) => LOOK[a.id]?.name ?? a.name);
  const late = o.agents.filter((a) => a.state === "late").map((a) => LOOK[a.id]?.name ?? a.name);
  const ideas = o.ideas_week.reduce((s, x) => s + x.n, 0);
  const parts = [`Hello. ${o.agents.filter((a) => a.state === "ok").length} of ${o.agents.length} agents are healthy.`];
  if (failed.length) parts.push(`${failed.join(" and ")} ${failed.length > 1 ? "have" : "has"} a failed run that needs a look.`);
  if (late.length) parts.push(`${late.join(" and ")} ${late.length > 1 ? "are" : "is"} running late.`);
  parts.push(`This week the fleet produced ${ideas} ideas.`);
  if (o.priority.p1) parts.push(`${o.priority.p1} ${o.priority.p1 > 1 ? "items are" : "item is"} marked do first; the top one is ${o.priority.top[0]?.title ?? ""}.`);
  if (o.fallback_24h) parts.push(`${o.fallback_24h} run${o.fallback_24h > 1 ? "s" : ""} used the backup model in the last day, so check those numbers before acting.`);
  if (o.customers) parts.push(`Fernway has ${o.customers.paying} paying customers, ${o.customers.winning} winning and ${o.customers.at_risk} at risk.`);
  if (o.fleet_paused) parts.push("The emergency stop is engaged, so nothing new will start until you resume.");
  if (o.quiet_hours) parts.push("It is quiet hours, so I am holding messages until seven.");
  return parts.join(" ");
}

export default function Deck({ data, reload }: { data: Overview | null; reload: () => void }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [avatarUp, setAvatarUp] = useState(false);
  useEffect(() => {
    const on = (e: Event) => setAvatarUp(Boolean((e as CustomEvent<boolean>).detail));
    window.addEventListener("claudia:avatar", on);
    return () => window.removeEventListener("claudia:avatar", on);
  }, []);
  const { toast, confirm } = useUi();
  if (!data) return <Loading />;
  const sel = data.agents.find((a) => a.id === selected) ?? null;

  const counts = data.agents.reduce<Record<string, number>>((m, a) => ((m[a.state] = (m[a.state] ?? 0) + 1), m), {});
  const ideasTotal = data.ideas_week.reduce((s, x) => s + x.n, 0);

  const runNow = async (jobHint: string) => {
    if (!sel) return;
    const ok = await confirm(`Run ${LOOK[sel.id]?.name}'s job now? It will use its normal model and may send to Telegram if that job delivers there.`);
    if (!ok) return;
    try {
      const d = await api.get<{ jobs: { id: string; name: string; state: string }[] }>(`/agents/${sel.id}`);
      const job = d.jobs.find((j) => j.state !== "paused" && !j.name.startsWith("Relay")) ?? d.jobs[0];
      if (!job) return toast("This agent has no jobs", "err");
      await api.post(`/jobs/${job.id}/run`);
      toast(`${job.name} queued ${jobHint}`);
      setTimeout(reload, 4000);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  return (
    <div className="relative h-full">
      <div className="absolute inset-0">
        <Suspense fallback={<Loading />}>
          <DeckScene agents={data.agents} quiet={data.quiet_hours} selected={selected} onSelect={setSelected} paused={avatarUp} />
        </Suspense>
      </div>

      {/* left HUD: fleet summary + Claudia */}
      <div className="scroll-thin pointer-events-auto absolute bottom-40 left-4 top-4 z-10 flex w-[290px] flex-col gap-3 overflow-y-auto pr-1">
        <div className="panel pointer-events-auto p-4">
          <div className="panel-title">Fleet</div>
          <div className="mt-3 grid grid-cols-4 gap-2 text-center">
            {(["ok", "late", "failed", "paused"] as const).map((k) => (
              <div key={k}>
                <div className="font-display text-xl" style={{ color: STATE_COLOR[k], textShadow: `0 0 12px ${STATE_COLOR[k]}` }}>
                  {k === "paused" ? (counts.paused ?? 0) + (counts.idle ?? 0) : counts[k] ?? 0}
                </div>
                <div className="hud-label">{k === "paused" ? "stby" : k}</div>
              </div>
            ))}
          </div>
          <div className="mt-4 grid grid-cols-2 gap-2 border-t border-line pt-3">
            <div>
              <div className="hud-label">Ideas · 7 days</div>
              <div className="font-display text-lg text-coral">{ideasTotal}</div>
            </div>
            <div>
              <div className="hud-label">Paying customers</div>
              <div className="font-display text-lg text-amber">{data.customers?.paying ?? "–"}</div>
            </div>
          </div>
        </div>

        {data.fallback_24h > 0 && (
          <div className="panel pointer-events-auto border-amber/60 px-4 py-2 text-[12px] text-amber">
            {data.fallback_24h} run{data.fallback_24h > 1 ? "s" : ""} on the backup model in 24h. The main model's quota is probably out; check facts in those reports.
          </div>
        )}

        {data.priority.top.length > 0 && (
          <div className="panel pointer-events-auto p-4">
            <div className="flex items-center justify-between">
              <div className="panel-title" style={{ color: "#ff4d6d" }}>Do first</div>
              <span className="hud-label">{data.priority.p1} P1 · {data.priority.p2} P2</span>
            </div>
            <ul className="mt-2 space-y-1.5">
              {data.priority.top.slice(0, 5).map((p) => (
                <li key={p.id}>
                  <Link to={`/agents/${p.agent === "backlog" ? "chief-assistant" : p.agent}#priority`} className="flex items-start gap-2 hover:text-cyan">
                    <span className="w-7 shrink-0 font-display text-sm" style={{ color: "#ff4d6d" }}>{p.score}</span>
                    <span className="min-w-0">
                      <span className="line-clamp-2 text-[12px] leading-snug text-ink/90">{p.title}</span>
                      <span className="hud-label" style={{ color: LOOK[p.agent]?.color }}>{LOOK[p.agent]?.name ?? "Claudia"}</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="panel pointer-events-auto p-4">
          <div className="flex items-center justify-between">
            <div className="panel-title" style={{ color: "#f4dca0" }}>Claudia</div>
            <span className="hud-label">voice {canSpeak() ? "online" : "unavailable"}</span>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <button className="btn btn-amber" onClick={() => speak("chief-assistant", briefing(data))} disabled={!canSpeak()}>Brief me</button>
            <button className="btn btn-ghost" onClick={stopSpeaking}>Hush</button>
          </div>
          <p className="mt-2 text-[12px] leading-relaxed text-dim">Talk to Claudia with the orb at the top, or switch on the wake word and just say “Claudia”.</p>
        </div>
      </div>

      {/* right: selected station */}
      {sel && (
        <div className="absolute right-4 top-4 z-10 w-[340px]">
          <div className="panel p-4">
            <img src={`/art/${sel.id}.webp`} alt="" aria-hidden
                 className="pointer-events-none absolute -right-6 -top-4 h-56 opacity-70 mix-blend-screen animate-flicker" />
            <div className="relative flex items-start justify-between gap-2">
              <div>
                <div className="font-display text-sm tracking-widest" style={{ color: LOOK[sel.id]?.color }}>{LOOK[sel.id]?.name.toUpperCase()}</div>
                <div className="hud-label mt-1">{LOOK[sel.id]?.station} · {sel.runs_on === "pc" ? "runs on your PC" : "server"}</div>
              </div>
              <button className="btn btn-ghost" onClick={() => setSelected(null)} aria-label="Close">×</button>
            </div>
            <p className="mt-3 text-sm text-ink/85">{sel.purpose}</p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <StateDot state={sel.state} pulse={sel.state !== "ok"} />
              <span className="font-mono text-xs uppercase" style={{ color: STATE_COLOR[sel.state] }}>{sel.state}</span>
              <Chip>next {due(sel.next_run_at)}</Chip>
              {sel.week.fallback > 0 && <Chip color="#f7b538">{sel.week.fallback} on backup model</Chip>}
            </div>
            {sel.last && (
              <div className="mt-3 border-l-2 pl-3" style={{ borderColor: LOOK[sel.id]?.color }}>
                <div className="hud-label">last · {sel.last.job_name} · {ago(sel.last.started_at)}</div>
                <div className="mt-1 text-sm text-ink/90">{sel.last.headline || "(no headline)"}</div>
              </div>
            )}
            <div className="mt-4 flex flex-wrap gap-2">
              <Link className="btn" to={`/agents/${sel.id}`}>Open station</Link>
              <button className="btn btn-amber" disabled={!canSpeak()}
                      onClick={() => speak(sel.id, sel.last ? `${LOOK[sel.id]?.name} reporting. ${sel.last.headline}` : "No report yet.")}>
                Speak
              </button>
              {sel.runs_on === "server" && <button className="btn btn-ghost" onClick={() => runNow("")}>Run now</button>}
            </div>
          </div>
        </div>
      )}

      {/* bottom: 24h timeline */}
      <div className="absolute inset-x-4 bottom-4 z-10">
        <div className="panel flex items-center gap-3 overflow-hidden px-3 py-2">
          <span className="panel-title shrink-0">24h</span>
          <div className="scroll-thin flex gap-2 overflow-x-auto">
            {data.timeline.length === 0 && <span className="hud-label">no runs in the last 24 hours</span>}
            {data.timeline.map((r, i) => (
              <button key={i} onClick={() => setSelected(r.agent)} title={r.headline}
                      className="flex shrink-0 items-center gap-2 border border-line/70 bg-deck/60 px-2 py-1 hover:border-cyan/60">
                <StateDot state={r.status === "failed" ? "failed" : r.used_fallback ? "late" : "ok"} />
                <span className="font-mono text-[10px] text-dim">{clock(r.started_at)}</span>
                <span className="font-display text-[9px] tracking-widest" style={{ color: LOOK[r.agent]?.color }}>
                  {(LOOK[r.agent]?.name ?? r.agent).toUpperCase()}
                </span>
                <span className="max-w-[180px] truncate text-[11px] text-ink/70">{r.job_name}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          {data.ideas_week.map((t) => <Chip key={t.type} color="#ff8a65">{TYPE_LABEL[t.type] ?? t.type}: {t.n}</Chip>)}
        </div>
      </div>
    </div>
  );
}
