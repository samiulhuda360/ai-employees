import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";
import { api, type Idea } from "../lib/api";
import { LOOK, TYPE_COLOR, TYPE_LABEL } from "../lib/fleet";
import { Chip, Drawer, Empty, Loading, Markdown, useUi } from "../components/ui";

interface IdeasResp { ideas: Idea[]; weeks: { week: string; n: number }[]; types: { type: string; n: number }[] }

const STATUSES: Idea["status"][] = ["new", "approved", "parked", "rejected", "done"];
const STATUS_COLOR: Record<string, string> = { new: "#29d9f5", approved: "#3ee6a8", parked: "#f7b538", rejected: "#ff4d6d", done: "#ff8a65" };

function weekLabel(w: string) {
  const m = /^(\d{4})-W(\d{2})$/.exec(w);
  if (!m) return w;
  const [y, n] = [Number(m[1]), Number(m[2])];
  const jan4 = new Date(Date.UTC(y, 0, 4));
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7) + (n - 1) * 7);
  const sunday = new Date(monday);
  sunday.setUTCDate(monday.getUTCDate() + 6);
  const f = (d: Date) => d.toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" });
  return `Week ${n} · ${f(monday)} – ${f(sunday)}`;
}

export default function Ideas({ agent }: { agent?: string } = {}) {
  const embedded = Boolean(agent);
  const [data, setData] = useState<IdeasResp | null>(null);
  const [f, setF] = useState({ week: "", agent: agent ?? "", type: "", status: "", q: "" });
  const [open, setOpen] = useState<Idea | null>(null);
  const [note, setNote] = useState("");
  const { toast } = useUi();

  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][]).toString();
  useEffect(() => {
    const t = setTimeout(() => api.get<IdeasResp>(`/ideas?${qs}`).then(setData).catch((e) => toast(e.message, "err")), 200);
    return () => clearTimeout(t);
  }, [qs]); // eslint-disable-line react-hooks/exhaustive-deps

  const grouped = useMemo(() => {
    const m = new Map<string, Idea[]>();
    for (const i of data?.ideas ?? []) {
      const k = i.week || "undated";
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(i);
    }
    return [...m.entries()];
  }, [data]);

  const openIdea = async (i: Idea) => {
    try {
      const full = await api.get<Idea>(`/ideas/${i.id}`);
      setOpen(full);
      setNote(full.note ?? "");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };
  const decide = async (status: Idea["status"]) => {
    if (!open) return;
    try {
      const upd = await api.post<Idea>(`/ideas/${open.id}`, { status, note });
      setOpen(upd);
      setData((d) => d && { ...d, ideas: d.ideas.map((x) => (x.id === upd.id ? { ...x, status: upd.status, note: upd.note } : x)) });
      toast(`Marked ${status}. Claudia will see it.`);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const agentOptions = ["backlog", ...Object.keys(LOOK)];
  return (
    <div className={embedded ? "flex flex-col" : "flex h-full flex-col"}>
      <div className={embedded ? "pb-4" : "border-b border-line/80 p-5 pb-4"}>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            {!embedded && <h1 className="font-display text-lg tracking-[.3em] text-ink glow-text">IDEAS</h1>}
            <p className="mt-1 text-sm text-dim">{embedded
              ? "Everything this agent has proposed or learned, week by week. Your decisions reach Claudia."
              : "Everything the fleet has proposed, week by week. Decide here; Claudia and the agents see your decisions."}</p>
          </div>
          <div className="flex gap-2">
            <a className="btn" href={`/api/ideas.xlsx?${qs}`}>Export .xlsx</a>
            <a className="btn btn-ghost" href={`/api/ideas.csv?${qs}`}>.csv</a>
          </div>
        </div>
        <div className={`mt-4 grid gap-2 sm:grid-cols-2 ${embedded ? "lg:grid-cols-4" : "lg:grid-cols-5"}`}>
          <select id="f-week" className="input" value={f.week} onChange={(e) => setF({ ...f, week: e.target.value })} aria-label="Week">
            <option value="">All weeks</option>
            {data?.weeks.map((w) => <option key={w.week} value={w.week}>{weekLabel(w.week)} ({w.n})</option>)}
          </select>
          {!embedded && (
            <select id="f-agent" className="input" value={f.agent} onChange={(e) => setF({ ...f, agent: e.target.value })} aria-label="Agent">
              <option value="">All agents</option>
              {agentOptions.map((a) => <option key={a} value={a}>{a === "backlog" ? "Ideas backlog" : LOOK[a]?.name ?? a}</option>)}
            </select>
          )}
          <select id="f-type" className="input" value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })} aria-label="Type">
            <option value="">All types</option>
            {data?.types.map((t) => <option key={t.type} value={t.type}>{TYPE_LABEL[t.type] ?? t.type} ({t.n})</option>)}
          </select>
          <select id="f-status" className="input" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })} aria-label="Status">
            <option value="">Any status</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <input id="f-q" className="input" placeholder="Search…" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} />
        </div>
      </div>

      <div className={embedded ? "" : "scroll-thin min-h-0 flex-1 overflow-y-auto p-5"}>
        {!data ? <Loading /> : grouped.length === 0 ? <Empty>{embedded ? "No ideas from this agent yet" : "No ideas match"}</Empty> : grouped.map(([week, list]) => (
          <section key={week} className="mb-8">
            <div className="mb-3 flex items-center gap-3">
              <h2 className="font-display text-xs tracking-[.3em] text-cyan">{weekLabel(week).toUpperCase()}</h2>
              <div className="h-px flex-1 bg-gradient-to-r from-cyan/40 to-transparent" />
              <span className="hud-label">{list.length} ideas</span>
            </div>
            <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
              {list.map((i) => (
                <button key={i.id} onClick={() => openIdea(i)} className="panel p-4 text-left transition hover:shadow-glow">
                  <div className="flex items-center justify-between gap-2">
                    <Chip color={TYPE_COLOR[i.type]}>{TYPE_LABEL[i.type] ?? i.type}</Chip>
                    <span className="font-mono text-[10px] uppercase" style={{ color: STATUS_COLOR[i.status] }}>● {i.status}</span>
                  </div>
                  <div className="mt-2 line-clamp-2 font-body text-[15px] font-medium leading-snug text-ink">{i.title}</div>
                  {i.summary && <p className="mt-1 line-clamp-3 text-[12.5px] leading-relaxed text-dim">{i.summary}</p>}
                  <div className="mt-3 hud-label">{i.agent === "backlog" ? "Ideas backlog" : LOOK[i.agent]?.name ?? i.agent} · {i.date}</div>
                </button>
              ))}
            </div>
          </section>
        ))}
      </div>

      <Drawer open={!!open} onClose={() => setOpen(null)} title={open ? TYPE_LABEL[open.type] ?? open.type : ""}>
        {open && (
          <div>
            <h3 className="font-body text-lg font-semibold leading-snug text-ink">{open.title}</h3>
            <div className="mt-2 flex flex-wrap gap-2">
              <Chip color={STATUS_COLOR[open.status]}>{open.status}</Chip>
              <Chip>{open.agent === "backlog" ? "Ideas backlog" : LOOK[open.agent]?.name ?? open.agent}</Chip>
              <Chip>{open.date}</Chip>
              {open.evidence && <Chip color="#ff8a65">{open.evidence.slice(0, 60)}</Chip>}
            </div>
            {open.summary && <p className="mt-4 text-sm leading-relaxed text-ink/90">{open.summary}</p>}
            {open.source_url && (
              <a className="mt-3 inline-block break-all font-mono text-xs text-cyan underline" href={open.source_url} target="_blank" rel="noreferrer">
                {open.source_url}
              </a>
            )}
            <div className="mt-5">
              <div className="hud-label mb-2">Decision</div>
              <div className="flex flex-wrap gap-2">
                {(["approved", "parked", "rejected", "done", "new"] as Idea["status"][]).map((s) => (
                  <button key={s} onClick={() => decide(s)} className={clsx("btn", s === "rejected" && "btn-alert", s === "parked" && "btn-amber", s === "new" && "btn-ghost")}
                          style={open.status === s ? { boxShadow: `0 0 0 1px ${STATUS_COLOR[s]}, 0 0 18px ${STATUS_COLOR[s]}55` } : undefined}>
                    {s === "new" ? "Reset" : s}
                  </button>
                ))}
              </div>
              <label className="mt-3 block">
                <span className="hud-label">Note (saved with the decision)</span>
                <textarea id="idea-note" className="input mt-1 min-h-[70px]" value={note} onChange={(e) => setNote(e.target.value)} />
              </label>
            </div>
            {open.detail && (
              <div className="mt-6 border-t border-line pt-4">
                <div className="hud-label mb-2">Full detail</div>
                <Markdown text={open.detail} />
              </div>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
}
