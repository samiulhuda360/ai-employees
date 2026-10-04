// Priority tab: an agent's open ideas ranked by hq_priority.py on the server.
// P1 = do first, P2 = worth doing. Every score shows the reasons behind it.

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { TYPE_LABEL } from "../lib/fleet";
import { Chip, Empty, Loading, Panel, useUi } from "../components/ui";

export interface PriorityItem {
  id: number;
  agent: string;
  date: string;
  type: string;
  title: string;
  summary: string;
  status: string;
  source_url: string | null;
  score: number;
  tier: "P1" | "P2" | "P3";
  why: string[];
  rechecking?: boolean;
  recheckable?: boolean;
  note?: string;
}
export interface PriorityData {
  items: PriorityItem[];
  counts: { P1: number; P2: number; P3: number };
  open: number;
  fixed?: { id: number; type: string; title: string; note: string; decided_at: string }[];
}

const TIER = {
  P1: { label: "P1 · do first", color: "#ff4d6d" },
  P2: { label: "P2 · worth doing", color: "#f7b538" },
  P3: { label: "P3", color: "#8aa0b8" },
} as const;

export default function Priority({ agent, data, onChanged }: { agent: string; data: PriorityData | null; onChanged: () => void }) {
  const { toast, confirm } = useUi();
  const [rechecking, setRechecking] = useState(false);

  // Run the agent's job again so fixed items drop off and new ones appear.
  const recheck = async () => {
    if (!(await confirm("Recheck now? The agent runs its job again; items you have fixed drop off this list and the new report goes to Telegram."))) return;
    setRechecking(true);
    try {
      const r = await api.post<{ where: string; job: string; eta: string }>(`/agents/${agent}/recheck`);
      toast(r.where === "pc" ? `${r.job}: sent to your PC (${r.eta})` : `${r.job}: running now (${r.eta})`);
      setTimeout(onChanged, 90_000);
      setTimeout(onChanged, 300_000);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setTimeout(() => setRechecking(false), 60_000);
    }
  };
  // Recheck one issue: the agent runs again; gone from its new report = fixed (green, then
  // off the list), still reported = stays with a note.
  const recheckOne = async (id: number) => {
    setBusy(id);
    try {
      const r = await api.post<{ where: string; eta: string }>(`/ideas/${id}/recheck`);
      toast(`Rechecking: ${r.eta}`);
      onChanged();
      for (const ms of [20_000, 40_000, 60_000, 90_000, 120_000, 180_000, 300_000, 600_000, 1_200_000]) setTimeout(onChanged, ms);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(null);
    }
  };

  const recheckBtn = agent !== "chief-assistant" && (
    <button className="btn btn-ghost shrink-0" onClick={recheck} disabled={rechecking}>{rechecking ? "Recheck sent…" : "Recheck all"}</button>
  );
  const [busy, setBusy] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => setOpen(null), [agent]);

  const decide = async (id: number, status: string) => {
    setBusy(id);
    try {
      await api.post(`/ideas/${id}`, { status });
      toast(`Marked ${status}`, "ok");
      onChanged();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(null);
    }
  };

  if (!data) return <Loading />;
  if (!data.items.length) return <div><div className="flex justify-end">{recheckBtn}</div><Empty>Nothing high priority right now ({data.open} open ideas, all low priority)</Empty></div>;
  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
      <p className="text-sm text-dim">
        Ranked by the signals this agent already reports (checks, verdicts, severity, fit), how fresh it is and how often
        it came up. <span className="text-ink">{data.counts.P1}</span> do first · <span className="text-ink">{data.counts.P2}</span> worth
        doing · {data.counts.P3} lower priority hidden. Done, parked and rejected ideas drop off.
      </p>
      {recheckBtn}
      </div>
      {data.fixed && data.fixed.length > 0 && (
        <Panel title={<span style={{ color: "#3ee6a8" }}>Fixed · confirmed by recheck</span>}>
          <ul className="space-y-2">
            {data.fixed.map((f) => (
              <li key={f.id} className="flex items-start gap-3">
                <span className="mt-0.5 font-display text-sm" style={{ color: "#3ee6a8" }}>✓</span>
                <div className="min-w-0">
                  <div className="text-sm text-ink/80 line-through decoration-mint/60">{f.title}</div>
                  <div className="font-mono text-[10px] text-mint/80">{f.note} · leaves this list after 24 hours</div>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      )}
      {(["P1", "P2"] as const).map((tier) => {
        const list = data.items.filter((i) => i.tier === tier);
        if (!list.length) return null;
        return (
          <Panel key={tier} title={<span style={{ color: TIER[tier].color }}>{TIER[tier].label}</span>}>
            <ul className="divide-y divide-line/60">
              {list.map((i) => (
                <li key={i.id} className="py-3">
                  <div className="flex items-start gap-3">
                    <div className="w-11 shrink-0 pt-0.5 text-center">
                      <div className="font-display text-lg leading-none" style={{ color: TIER[i.tier].color }}>{i.score}</div>
                      <div className="mt-1 h-1 bg-line"><div className="h-1" style={{ width: `${i.score}%`, background: TIER[i.tier].color }} /></div>
                    </div>
                    <div className="min-w-0 flex-1">
                      <button className="text-left text-sm text-ink hover:text-cyan" onClick={() => setOpen(open === i.id ? null : i.id)}>
                        {i.title}
                      </button>
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        <Chip>{TYPE_LABEL[i.type] ?? i.type}</Chip>
                        {i.status === "approved" && <Chip color="#3ee6a8">approved</Chip>}
                        {i.rechecking && <Chip color="#f7b538">rechecking…</Chip>}
                        {!i.rechecking && i.note?.startsWith("Still there") && <Chip color="#ff4d6d">{i.note.slice(0, 120)}</Chip>}
                        {!i.rechecking && i.note?.startsWith("Recheck did not") && <Chip color="#7894b3">recheck did not complete</Chip>}
                        {i.why.map((w) => <Chip key={w} color="#ff8a65">{w}</Chip>)}
                      </div>
                      {open === i.id && (
                        <div className="mt-2 text-[13px] text-ink/80">
                          {i.summary && <p>{i.summary}</p>}
                          {i.source_url && <a className="mt-1 block text-cyan hover:underline" href={i.source_url} target="_blank" rel="noreferrer">source</a>}
                          <div className="mt-1 font-mono text-[11px] text-dim">{i.date}</div>
                        </div>
                      )}
                    </div>
                    <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
                      {i.recheckable && (
                        <button className="btn btn-amber px-2 py-0.5" disabled={busy === i.id || i.rechecking} onClick={() => recheckOne(i.id)}
                                title="Run the agent again and see whether this is fixed">{i.rechecking ? "Checking…" : "Recheck"}</button>
                      )}
                      {i.status !== "approved" && (
                        <button className="btn px-2 py-0.5" disabled={busy === i.id} onClick={() => decide(i.id, "approved")}>Approve</button>
                      )}
                      <button className="btn btn-ghost px-2 py-0.5" disabled={busy === i.id} onClick={() => decide(i.id, "done")}>Done</button>
                      <button className="btn btn-ghost px-2 py-0.5" disabled={busy === i.id} onClick={() => decide(i.id, "parked")}>Park</button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </Panel>
        );
      })}
    </div>
  );
}
