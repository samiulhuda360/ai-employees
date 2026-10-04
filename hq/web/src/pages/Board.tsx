// Build and Write boards: every idea of one kind from every agent, in one ranked list.
//   Build  things you could make (cash builds, sell-what-you-have, product features, SaaS ideas)
//   Write  blog ideas (Tier A first), with the writer agent's status
// Repeats of the same idea are merged; opening one shows all the research behind it.

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { LOOK, TYPE_COLOR, TYPE_LABEL } from "../lib/fleet";
import { Chip, Drawer, Empty, Loading, Markdown, Panel, useUi } from "../components/ui";

interface Mention { id: number; date: string; agent: string; evidence: string; source_url: string | null; detail: string; summary: string }
interface BoardItem {
  id: number; agent: string; date: string; type: string; title: string; summary: string; status: string;
  source_url: string | null; score: number; tier: "P1" | "P2" | "P3"; why: string[]; note: string;
  facts: Record<string, string>; mentions: Mention[];
}
interface BoardData { items: BoardItem[]; counts: Record<string, number>; total: number }

const KIND = {
  build: { title: "BUILD", blurb: "Everything you could make, best first: cash builds, things you already own and could sell, product features and SaaS ideas, from every agent. Repeats are merged." },
  write: { title: "WRITE", blurb: "Every blog idea still worth writing, best first. Tier A means the top 10 was checked and is beatable. Ideas your writer has written or rejected drop off." },
} as const;
const TIER_COLOR = { P1: "#ff4d6d", P2: "#f7b538", P3: "#7894b3" } as const;
// The score's reasons, minus the ones already shown as a type or fact label.
const reasons = (why: string[]) => why.slice(1).filter((w) => !/days? to build|^tier [ab]/i.test(w));

export default function Board({ kind }: { kind: "build" | "write" }) {
  const [data, setData] = useState<BoardData | null>(null);
  const [type, setType] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<BoardItem | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const { toast } = useUi();

  const load = () => api.get<BoardData>(`/board?kind=${kind}`).then(setData).catch((e) => toast(e.message, "err"));
  useEffect(() => { setData(null); setType(""); setQ(""); setOpen(null); load(); }, [kind]); // eslint-disable-line react-hooks/exhaustive-deps

  const decide = async (i: BoardItem, status: string) => {
    setBusy(i.id);
    try {
      // One decision covers every repeat of the idea.
      await Promise.all(i.mentions.map((m) => api.post(`/ideas/${m.id}`, { status })));
      toast(`Marked ${status}`);
      setOpen(null);
      load();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(null);
    }
  };

  if (!data) return <Loading />;
  const needle = q.trim().toLowerCase();
  const items = data.items.filter((i) => (!type || i.type === type) && (!needle || `${i.title} ${i.summary}`.toLowerCase().includes(needle)));

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-line/80 p-5 pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="font-display text-lg tracking-[.3em] text-ink glow-text">{KIND[kind].title}</h1>
            <p className="mt-1 max-w-3xl text-sm text-dim">{KIND[kind].blurb}</p>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button className={`btn ${type === "" ? "" : "btn-ghost"}`} onClick={() => setType("")}>All · {data.total}</button>
          {Object.entries(data.counts).map(([t, n]) => (
            <button key={t} className={`btn ${type === t ? "" : "btn-ghost"}`} onClick={() => setType(t)}>{TYPE_LABEL[t] ?? t} · {n}</button>
          ))}
          <input id="board-q" className="input ml-auto w-64" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" />
          <a className="btn" href={`/api/board.csv?kind=${kind}`}>Export .csv</a>
        </div>
      </div>

      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto p-5">
        {items.length === 0 ? <Empty>Nothing here yet</Empty> : (
          <Panel>
            <ul className="divide-y divide-line/60">
              {items.map((i, n) => (
                <li key={i.id} className="py-3">
                  <div className="flex items-start gap-3">
                    <div className="w-6 shrink-0 pt-1 text-right font-mono text-[11px] text-dim">{n + 1}</div>
                    <div className="w-11 shrink-0 pt-0.5 text-center">
                      <div className="font-display text-lg leading-none" style={{ color: TIER_COLOR[i.tier] }}>{i.score}</div>
                      <div className="mt-1 h-1 bg-line"><div className="h-1" style={{ width: `${i.score}%`, background: TIER_COLOR[i.tier] }} /></div>
                    </div>
                    <div className="min-w-0 flex-1">
                      <button className="text-left text-[15px] text-ink hover:text-cyan" onClick={() => setOpen(i)}>{i.title}</button>
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        <Chip color={TYPE_COLOR[i.type]}>{TYPE_LABEL[i.type] ?? i.type}</Chip>
                        {Object.entries(i.facts).map(([k, v]) => <Chip key={k} color="#f4dca0">{k}: {v}</Chip>)}
                        {i.mentions.length > 1 && <Chip color="#3ee6a8">raised {i.mentions.length} times</Chip>}
                        {i.status === "approved" && <Chip color="#3ee6a8">approved</Chip>}
                        {reasons(i.why).slice(0, 4).map((w) => <Chip key={w} color="#ff8a65">{w}</Chip>)}
                      </div>
                      {i.note && <div className="mt-1 font-mono text-[11px] text-dim">note: {i.note}</div>}
                    </div>
                    <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
                      {i.status !== "approved" && <button className="btn px-2 py-0.5" disabled={busy === i.id} onClick={() => decide(i, "approved")}>Approve</button>}
                      <button className="btn btn-ghost px-2 py-0.5" disabled={busy === i.id} onClick={() => decide(i, "done")}>Done</button>
                      <button className="btn btn-ghost px-2 py-0.5" disabled={busy === i.id} onClick={() => decide(i, "parked")}>Park</button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </Panel>
        )}
      </div>

      <Drawer open={!!open} onClose={() => setOpen(null)} title={open?.title ?? ""}>
        {open && (
          <>
            <div className="mb-4 flex flex-wrap gap-2">
              <Chip color={TIER_COLOR[open.tier]}>score {open.score}</Chip>
              <Chip color={TYPE_COLOR[open.type]}>{TYPE_LABEL[open.type] ?? open.type}</Chip>
              {reasons(open.why).map((w) => <Chip key={w} color="#ff8a65">{w}</Chip>)}
            </div>
            <div className="mb-4 flex flex-wrap gap-2">
              <button className="btn" onClick={() => decide(open, "approved")}>Approve</button>
              <button className="btn btn-ghost" onClick={() => decide(open, "done")}>Done</button>
              <button className="btn btn-ghost" onClick={() => decide(open, "parked")}>Park</button>
              <button className="btn btn-ghost" onClick={() => decide(open, "rejected")}>Reject</button>
            </div>
            <div className="panel-title mb-2">Research · {open.mentions.length} report{open.mentions.length > 1 ? "s" : ""}</div>
            {open.mentions.map((m) => (
              <div key={m.id} className="mb-4 border-l-2 pl-3" style={{ borderColor: LOOK[m.agent]?.color ?? "#29d9f5" }}>
                <div className="hud-label">{LOOK[m.agent]?.name ?? m.agent} · {m.date} · {m.evidence}</div>
                {m.source_url && <a className="break-all text-[12px] text-cyan hover:underline" href={m.source_url} target="_blank" rel="noreferrer">{m.source_url}</a>}
                <div className="mt-1"><Markdown text={m.detail || m.summary || ""} /></div>
              </div>
            ))}
          </>
        )}
      </Drawer>
    </div>
  );
}
