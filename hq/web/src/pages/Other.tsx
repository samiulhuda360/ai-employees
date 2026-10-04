import { useEffect, useState } from "react";
import { api, type Prospect } from "../lib/api";
import { Chip, Empty, Loading, Markdown, Panel, Stat, useUi } from "../components/ui";

// ---------------------------------------------------------------- prospects

const P_STATUS = ["suggested", "contacted", "replied", "won", "no fit"];
const P_COLOR: Record<string, string> = { suggested: "#29d9f5", contacted: "#ff8a65", replied: "#f7b538", won: "#3ee6a8", "no fit": "#7894b3" };

export function Prospects() {
  const [list, setList] = useState<Prospect[] | null>(null);
  const { toast } = useUi();
  useEffect(() => { api.get<{ prospects: Prospect[] }>("/prospects").then((r) => setList(r.prospects)).catch((e) => toast(e.message, "err")); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const setStatus = async (p: Prospect, status: string) => {
    try {
      await api.post(`/prospects/${encodeURIComponent(p.cid)}`, { status });
      setList((l) => l && l.map((x) => (x.cid === p.cid ? { ...x, status } : x)));
      toast(`${p.business}: ${status}`);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };
  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); toast("Copied"); } catch { toast("Copy blocked by the browser", "err"); }
  };

  if (!list) return <Loading />;
  const counts = P_STATUS.map((s) => [s, list.filter((p) => p.status === s).length] as const);
  return (
    <div className="scroll-thin h-full overflow-y-auto p-5">
      <h1 className="font-display text-lg tracking-[.3em] text-ink glow-text">PROSPECTS</h1>
      <p className="mt-1 text-sm text-dim">Local businesses with weak Google Business Profiles. You do the outreach; track it here.</p>
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {counts.map(([s, n]) => <Stat key={s} label={s} value={n} color={P_COLOR[s]} />)}
      </div>
      {list.length === 0 ? <Empty>No prospects yet. Prospect Finder runs weekdays at 08:45.</Empty> : (
        <div className="mt-5 grid gap-3">
          {list.map((p) => (
            <div key={p.cid} className="panel p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="font-body text-[15px] font-semibold text-ink">{p.business}</div>
                  <div className="hud-label mt-1">{p.niche} · {p.city} · Maps #{p.rank} · {p.reviews} reviews · {p.rating}★ · found {p.date}</div>
                </div>
                <select id={`ps-${p.cid}`} className="input w-40" value={p.status} onChange={(e) => setStatus(p, e.target.value)} aria-label="Status"
                        style={{ color: P_COLOR[p.status] }}>
                  {P_STATUS.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {p.signals.split(" | ").map((s) => <Chip key={s} color="#f7b538">{s}</Chip>)}
              </div>
              <div className="mt-3 flex flex-wrap gap-2 font-mono text-xs">
                {p.website && <a className="btn btn-ghost" href={p.website} target="_blank" rel="noreferrer">Website</a>}
                {p.phone && <button className="btn btn-ghost" onClick={() => copy(p.phone)}>{p.phone}</button>}
                {p.email && <button className="btn btn-ghost" onClick={() => copy(p.email)}>{p.email}</button>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- customers

interface CustResp {
  weeks: { date: string; accounts: number; paying: number; revenue_usd: number; winning: number; at_risk: number }[];
  signals: { id: number; date: string; type: string; title: string; summary: string }[];
}

export function Customers() {
  const [d, setD] = useState<CustResp | null>(null);
  const { toast } = useUi();
  useEffect(() => { api.get<CustResp>("/customers").then(setD).catch((e) => toast(e.message, "err")); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  if (!d) return <Loading />;
  const last = d.weeks[d.weeks.length - 1];
  const latestDate = d.signals[0]?.date;
  const latest = d.signals.filter((s) => s.date === latestDate);
  const group = (t: string) => latest.filter((s) => s.type === t);
  const max = Math.max(1, ...d.weeks.map((w) => w.paying));
  return (
    <div className="scroll-thin h-full overflow-y-auto p-5">
      <h1 className="font-display text-lg tracking-[.3em] text-ink glow-text">CUSTOMERS</h1>
      <p className="mt-1 text-sm text-dim">Fernway customer health from the weekly pulse. Team and test accounts are excluded.</p>
      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Paying" value={last?.paying ?? "–"} color="#f7b538" max={last?.accounts} />
        <Stat label="Accounts" value={last?.accounts ?? "–"} />
        <Stat label="Winning" value={last?.winning ?? "–"} color="#3ee6a8" max={last?.paying} />
        <Stat label="At risk" value={last?.at_risk ?? "–"} color="#ff4d6d" max={last?.paying} />
        <Stat label="Lifetime revenue" value={last ? `$${Math.round(last.revenue_usd).toLocaleString()}` : "–"} color="#f4dca0" />
      </div>
      <Panel title="Paying customers by week" className="mt-5">
        {d.weeks.length < 2 ? <p className="text-sm text-dim">The trend starts after the second weekly report.</p> : (
          <div className="flex items-end gap-3">
            {d.weeks.map((w) => (
              <div key={w.date} className="flex w-16 flex-col items-center gap-1" title={`${w.date}: ${w.paying} paying, ${w.at_risk} at risk`}>
                <span className="font-mono text-[11px] text-amber">{w.paying}</span>
                <div className="w-full bg-amber/70" style={{ height: `${Math.max(6, Math.round((w.paying / max) * 110))}px`, boxShadow: "0 0 12px #f7b53866" }} />
                <span className="font-mono text-[9px] text-dim">{w.date.slice(5)}</span>
              </div>
            ))}
          </div>
        )}
      </Panel>
      <div className="mt-5 grid gap-4 lg:grid-cols-3">
        {([["customer_win", "Winning", "#3ee6a8"], ["customer_risk", "At risk", "#ff4d6d"], ["customer_lead", "Not yet paying", "#f7b538"]] as const).map(([t, label, c]) => (
          <Panel key={t} title={<span style={{ color: c }}>{label} · {latestDate ?? ""}</span>}>
            {group(t).length === 0 ? <Empty>None</Empty> : (
              <ul className="space-y-3">
                {group(t).map((s) => (
                  <li key={s.id}>
                    <div className="text-sm font-semibold text-ink">{s.title}</div>
                    <div className="text-[12.5px] leading-relaxed text-dim">{s.summary}</div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- me

interface MeResp { journal: string; inbox: string; about: string; records: { name: string; text: string }[] }

export function Me() {
  const [d, setD] = useState<MeResp | null>(null);
  const [rec, setRec] = useState(0);
  const { toast } = useUi();
  useEffect(() => { api.get<MeResp>("/me/journal").then(setD).catch((e) => toast(e.message, "err")); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  if (!d) return <Loading />;
  return (
    <div className="scroll-thin h-full overflow-y-auto p-5">
      <h1 className="font-display text-lg tracking-[.3em] text-ink glow-text">ME</h1>
      <p className="mt-1 text-sm text-dim">Your plans, what got done, your task inbox, and the weekly and monthly records Claudia keeps.</p>
      <div className="mt-5 grid gap-4 xl:grid-cols-2">
        <Panel title="Journal"><Markdown text={d.journal || "_Nothing logged yet. Reply `plan:` or `done:` to Claudia on Telegram._"} /></Panel>
        <div className="grid gap-4">
          <Panel title="Task inbox"><Markdown text={d.inbox || "_Empty. Send `inbox: ...` to Claudia._"} /></Panel>
          <Panel title="What Claudia knows about you"><Markdown text={d.about} /></Panel>
        </div>
      </div>
      <Panel title="Records" className="mt-4" right={d.records.length > 0 && (
        <select id="rec" className="input w-56" value={rec} onChange={(e) => setRec(Number(e.target.value))} aria-label="Record">
          {d.records.map((r, i) => <option key={r.name} value={i}>{r.name}</option>)}
        </select>
      )}>
        {d.records.length === 0 ? <Empty>The first weekly record is written on Sunday at 17:00</Empty> : <Markdown text={d.records[rec]?.text ?? ""} />}
      </Panel>
    </div>
  );
}

// ---------------------------------------------------------------- log

// Voice replies are stored as JSON; show what Claudia said, not the escapes.
function readable(result: string): string {
  try {
    const j = JSON.parse(result);
    if (j && typeof j.say === "string") return `"${j.say}"${j.action ? ` -> ${j.action.type}` : ""}`;
  } catch { /* plain text */ }
  return result;
}

export function Log() {
  const [rows, setRows] = useState<{ id: number; at: string; action: string; target: string; args: string; result: string }[] | null>(null);
  useEffect(() => { api.get<{ audit: typeof rows }>("/audit").then((r) => setRows(r.audit ?? [])).catch(() => setRows([])); }, []);
  if (!rows) return <Loading />;
  return (
    <div className="scroll-thin h-full overflow-y-auto p-5">
      <h1 className="font-display text-lg tracking-[.3em] text-ink glow-text">LOG</h1>
      <p className="mt-1 text-sm text-dim">Every action taken from HQ: who, what, when, and what Hermes answered.</p>
      <Panel className="mt-5" title="Audit trail">
        {rows.length === 0 ? <Empty>No actions yet</Empty> : (
          <table className="w-full text-left text-[12px]">
            <thead><tr className="hud-label"><th className="py-1 pr-3">When</th><th className="pr-3">Action</th><th className="pr-3">Target</th><th>Result</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-t border-line/60 align-top">
                  <td className="py-1.5 pr-3 font-mono text-dim">{r.at.slice(0, 16).replace("T", " ")}</td>
                  <td className="pr-3 font-mono text-cyan">{r.action}</td>
                  <td className="pr-3 text-ink/85">{r.target}</td>
                  <td className="max-w-[560px] truncate font-mono text-dim" title={r.result}>{readable(r.result)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
