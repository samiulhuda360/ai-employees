import clsx from "clsx";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ago, api, clock, due, type AgentDetail, type Job, type Overview, type Run } from "../lib/api";
import { LOOK, STATE_COLOR } from "../lib/fleet";
import { canSpeak, speak } from "../lib/voice";
import { Chip, Drawer, Empty, Loading, Markdown, Panel, StateDot, useUi } from "../components/ui";
import Ideas from "./Ideas";
import Priority, { type PriorityData } from "./Priority";

export function AgentsList({ data }: { data: Overview | null }) {
  if (!data) return <Loading />;
  return (
    <div className="scroll-thin h-full overflow-y-auto p-5">
      <h1 className="font-display text-lg tracking-[.3em] text-ink glow-text">AGENTS</h1>
      <p className="mt-1 text-sm text-dim">Every agent in the fleet. Open one to see its plan, its brain, its runs, and to control it.</p>
      <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {data.agents.map((a) => {
          const look = LOOK[a.id];
          return (
            <Link key={a.id} to={`/agents/${a.id}`} className="panel block p-4 transition hover:shadow-glow">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-display text-sm tracking-widest" style={{ color: look?.color }}>{(look?.name ?? a.name).toUpperCase()}</div>
                  <div className="hud-label mt-1">{look?.station} · {a.runs_on === "pc" ? "PC" : "server"}</div>
                </div>
                <div className="flex items-center gap-2">
                  <StateDot state={a.state} pulse={a.state === "failed"} />
                  <span className="font-mono text-[10px] uppercase" style={{ color: STATE_COLOR[a.state] }}>{a.state}</span>
                </div>
              </div>
              <p className="mt-3 text-sm text-ink/80">{a.purpose}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {a.p1 > 0 && <Chip color="#ff4d6d">{a.p1} do first</Chip>}
                <Chip>{a.jobs} jobs</Chip>
                <Chip>next {due(a.next_run_at)}</Chip>
                <Chip color="#ff8a65">{a.week.n} runs / 7d</Chip>
                {a.week.failed_24h > 0 ? <Chip color="#ff4d6d">{a.week.failed_24h} failed today</Chip>
                  : a.week.failed > 0 ? <Chip color="#7894b3">{a.week.failed} failed earlier this week</Chip> : null}
                {a.week.fallback > 0 && <Chip color="#f7b538">{a.week.fallback} backup model</Chip>}
              </div>
              {a.last && <p className="mt-3 line-clamp-2 text-[12px] text-dim">{a.last.headline}</p>}
            </Link>
          );
        })}
      </div>
    </div>
  );
}

type Tab = "jobs" | "priority" | "ideas" | "runs" | "plan" | "brain";

export function AgentStation({ onChanged }: { onChanged: () => void }) {
  const { id = "" } = useParams();
  const [d, setD] = useState<AgentDetail | null>(null);
  const [tab, setTab] = useState<Tab>("jobs");
  const [run, setRun] = useState<Run | null>(null);
  const [prio, setPrio] = useState<PriorityData | null>(null);
  const { toast } = useUi();
  const look = LOOK[id];

  const load = () => api.get<AgentDetail>(`/agents/${id}`).then(setD).catch((e) => toast(e.message, "err"));
  const loadPrio = () => api.get<PriorityData>(`/priority?agent=${encodeURIComponent(id)}`).then(setPrio).catch(() => setPrio(null));
  const hashTab = (): Tab => (["priority", "ideas", "runs", "plan", "brain"].includes(location.hash.slice(1)) ? (location.hash.slice(1) as Tab) : "jobs");
  useEffect(() => { setD(null); setPrio(null); setTab(hashTab()); load(); loadPrio(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  const tabs: Tab[] = prio && prio.open > 0 ? ["jobs", "priority", "ideas", "runs", "plan", "brain"] : ["jobs", "ideas", "runs", "plan", "brain"];

  const openRun = async (r: Run) => {
    try {
      setRun(await api.get<Run>(`/runs/${r.id}`));
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  if (!d) return <Loading />;
  return (
    <div className="scroll-thin h-full overflow-y-auto p-5">
      <Link to="/agents" className="hud-label hover:text-cyan">← all agents</Link>
      <div className="mt-2 flex flex-wrap items-end justify-between gap-4">
        <div className="flex items-end gap-4">
          <img src={`/art/${id}.webp`} alt={`${look?.name} hologram`} className="h-28 mix-blend-screen animate-flicker" />
          <div>
          <h1 className="font-display text-xl tracking-[.3em]" style={{ color: look?.color, textShadow: `0 0 16px ${look?.color}88` }}>
            {(look?.name ?? d.name).toUpperCase()}
          </h1>
          <p className="mt-1 text-sm text-dim">{d.purpose} · {look?.station} · {d.runs_on === "pc" ? "runs on your PC (read-only here)" : "runs on the server"}</p>
          </div>
        </div>
        <button className="btn btn-amber" disabled={!canSpeak() || !d.runs[0]}
                onClick={() => speak(id, `${look?.name} reporting. ${d.runs[0]?.headline ?? ""}`)}>Speak last report</button>
      </div>

      <div className="mt-5 flex gap-2 border-b border-line" role="tablist">
        {tabs.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                  className={clsx("px-4 py-2 font-display text-[11px] uppercase tracking-[.25em] transition",
                    tab === t ? "border-b-2 border-cyan text-cyan" : "text-dim hover:text-ink")}>
            {t}
            {t === "priority" && prio && prio.counts.P1 > 0 && (
              <span className="ml-2 rounded-sm px-1.5 py-px font-mono text-[10px] tracking-normal text-white" style={{ background: "#ff4d6d" }}>{prio.counts.P1}</span>
            )}
          </button>
        ))}
      </div>

      <div className="mt-5">
        {tab === "jobs" && <Jobs jobs={d.jobs} remote={d.runs_on === "pc"} onChanged={() => { load(); onChanged(); }} />}
        {tab === "priority" && <Priority agent={id} data={prio} onChanged={loadPrio} />}
        {tab === "ideas" && <Ideas key={id} agent={id === "chief-assistant" ? "backlog" : id} />}
        {tab === "runs" && (
          <Panel title="Run history">
            {d.runs.length === 0 ? <Empty>No runs recorded yet</Empty> : (
              <ul className="divide-y divide-line/60">
                {d.runs.map((r) => (
                  <li key={r.id}>
                    <button onClick={() => openRun(r)} className="flex w-full items-start gap-3 py-2 text-left hover:bg-cyan/5">
                      <StateDot state={r.status === "failed" ? "failed" : r.used_fallback ? "late" : "ok"} />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-[11px] text-dim">{r.started_at.slice(0, 10)} {clock(r.started_at)}</span>
                          <span className="font-display text-[10px] tracking-widest text-cyan/80">{r.job_name}</span>
                          {r.used_fallback ? <Chip color="#f7b538">backup: {r.fallback_model}</Chip> : null}
                        </div>
                        <div className="mt-0.5 truncate text-sm text-ink/85">{r.headline}</div>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        )}
        {tab === "plan" && (
          <Panel title="Plan · SOUL (how this agent thinks and works)">
            {d.soul ? <Markdown text={d.soul} /> : <Empty>No SOUL file found</Empty>}
          </Panel>
        )}
        {tab === "brain" && <Brain brain={d.brain} color={look?.color ?? "#29d9f5"} />}
      </div>

      <Drawer open={!!run} onClose={() => setRun(null)} title={run ? `${run.job_name} · ${run.started_at.slice(0, 16).replace("T", " ")}` : ""}>
        {run && (
          <>
            <div className="mb-3 flex flex-wrap gap-2">
              <Chip color={run.status === "failed" ? "#ff4d6d" : "#3ee6a8"}>{run.status}</Chip>
              {run.used_fallback ? <Chip color="#f7b538">backup model: {run.fallback_model}</Chip> : <Chip>primary model</Chip>}
              <button className="btn btn-ghost ml-auto" disabled={!canSpeak()} onClick={() => speak(id, (run.body ?? "").slice(0, 900))}>Read aloud</button>
            </div>
            <Markdown text={run.body ?? ""} />
          </>
        )}
      </Drawer>
    </div>
  );
}

function Jobs({ jobs, remote, onChanged }: { jobs: Job[]; remote: boolean; onChanged: () => void }) {
  const { confirm, toast } = useUi();
  const [editing, setEditing] = useState<string | null>(null);
  const [sched, setSched] = useState("");

  const act = async (j: Job, action: "run" | "pause" | "resume") => {
    const text = action === "run" ? `Run "${j.name}" now?` : action === "pause" ? `Pause "${j.name}"? It will not run until resumed.` : `Resume "${j.name}"?`;
    if (!(await confirm(text))) return;
    try {
      await api.post(`/jobs/${j.id}/${action}`);
      toast(`${j.name}: ${action} sent`);
      setTimeout(onChanged, 2500);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };
  const saveSchedule = async (j: Job) => {
    if (!(await confirm(`Change "${j.name}" from "${j.schedule}" to "${sched}"?`))) return;
    try {
      await api.post(`/jobs/${j.id}/schedule`, { schedule: sched });
      toast("Schedule updated");
      setEditing(null);
      setTimeout(onChanged, 2500);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  if (jobs.length === 0) return <Empty>This agent has no scheduled jobs</Empty>;
  return (
    <div className="grid gap-3">
      {remote && <p className="font-mono text-[11px] text-amber">These jobs run on your PC. HQ shows their live status; control them on the PC.</p>}
      {jobs.map((j) => (
        <div key={j.id} className="panel p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <StateDot state={j.health} pulse={j.health === "failed"} />
              <div>
                <div className="font-display text-xs tracking-widest text-ink">{j.name}</div>
                <div className="hud-label mt-1">
                  {j.schedule} · {j.deliver} · {j.no_agent ? "script only" : j.model ? `pinned ${j.model}` : "fleet model"}
                </div>
              </div>
            </div>
            {!remote && (
              <div className="flex flex-wrap gap-2">
                <button className="btn" onClick={() => act(j, "run")}>Run now</button>
                {j.state === "paused"
                  ? <button className="btn btn-amber" onClick={() => act(j, "resume")}>Resume</button>
                  : <button className="btn btn-ghost" onClick={() => act(j, "pause")}>Pause</button>}
                <button className="btn btn-ghost" onClick={() => { setEditing(j.id); setSched(j.schedule); }}>Schedule</button>
              </div>
            )}
          </div>
          <div className="mt-3 grid gap-2 font-mono text-[11px] text-dim sm:grid-cols-3">
            <div>last run: <span className="text-ink">{ago(j.last_run_at)}</span> ({j.last_status ?? "–"})</div>
            <div>next run: <span className="text-ink">{j.next_run_at ? `${j.next_run_at.slice(0, 10)} ${clock(j.next_run_at)}` : "–"}</span></div>
            <div>state: <span className="text-ink">{j.state}</span></div>
          </div>
          {j.last_error && <p className="mt-2 font-mono text-[11px] text-alert">{j.last_error}</p>}
          {editing === j.id && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <input id={`sched-${j.id}`} className="input max-w-xs" value={sched} onChange={(e) => setSched(e.target.value)}
                     aria-label="Cron schedule" placeholder="30 9 * * 1-5" />
              <button className="btn btn-amber" onClick={() => saveSchedule(j)}>Save</button>
              <button className="btn btn-ghost" onClick={() => setEditing(null)}>Cancel</button>
              <span className="hud-label">minute hour day month weekday · server time</span>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Brain({ brain, color }: { brain: Record<string, string[]>; color: string }) {
  const sections = Object.entries(brain);
  if (sections.length === 0) return <Empty>This agent has no long-term memory yet</Empty>;
  const label: Record<string, string> = { "MEMORY.md": "What it has learned", "USER.md": "What it knows about you", "knowledge-base": "Knowledge base (latest entries)" };
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {sections.map(([name, entries]) => (
        <Panel key={name} title={label[name] ?? name} right={<span className="hud-label">{entries.length} nodes</span>}>
          <ul className="space-y-2">
            {entries.map((e, i) => (
              <li key={i} className="flex gap-3 text-[13px] leading-snug text-ink/85">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: color, boxShadow: `0 0 8px ${color}` }} />
                <span>{e}</span>
              </li>
            ))}
          </ul>
        </Panel>
      ))}
    </div>
  );
}
