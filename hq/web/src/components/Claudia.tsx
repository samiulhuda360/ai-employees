// Claudia's orb in the top bar: the voice assistant for the whole of HQ.
//   Click          push to talk once
//   Wake switch    listen continuously for "Claudia" while HQ is open
// What you say goes to Claudia's real Hermes brain (/api/ask). She answers out loud in
// her server voice and may propose one allowlisted action: open a page, run a job,
// decide an idea, or log plan/done. Anything that runs or changes something is confirmed
// on screen first. She never drafts or sends messages to customers.

import clsx from "clsx";
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { LOOK } from "../lib/fleet";
import { canListen, emitLevel, listenOnce, speak, startWakeWord, stopSpeaking, type WakeHandle } from "../lib/voice";
import { useUi } from "./ui";

type Phase = "idle" | "listening" | "heard" | "thinking" | "speaking";
interface Action { type: string; path?: string; agent?: string; idea_id?: number | string; status?: string; kind?: string; text?: string }

const PREF = "hq.wakeword";
const readPref = () => { try { return localStorage.getItem(PREF) === "on"; } catch { return false; } };
const writePref = (on: boolean) => { try { localStorage.setItem(PREF, on ? "on" : "off"); } catch { /* private mode */ } };

export default function Claudia() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [wake, setWake] = useState(false);
  const [heard, setHeard] = useState("");
  const [reply, setReply] = useState("");
  const [err, setErr] = useState("");
  const [live, setLive] = useState("");
  const [tapListening, setTapListening] = useState(false);
  const wakeRef = useRef<WakeHandle | null>(null);
  const busy = useRef(false);
  const nav = useNavigate();
  const loc = useLocation();
  const { confirm, toast } = useUi();

  // Tell the avatar panel what Claudia is doing.
  useEffect(() => {
    window.dispatchEvent(new CustomEvent("claudia:phase", { detail: phase }));
  }, [phase]);

  const act = useCallback(async (a: Action) => {
    try {
      if (a.type === "navigate" && a.path) nav(a.path);
      if (a.type === "log" && a.kind && a.text) {
        await api.post("/me/log", { kind: a.kind, text: a.text });
        toast(`Logged ${a.kind}: ${a.text}`);
      }
      if (a.type === "run_job" && a.agent) {
        const name = LOOK[a.agent]?.name ?? a.agent;
        if (!(await confirm(`Claudia wants to run ${name} now. Go ahead?`))) return;
        const d = await api.get<{ jobs: { id: string; name: string; state: string }[] }>(`/agents/${a.agent}`);
        const job = d.jobs.find((j) => j.state !== "paused" && !j.name.startsWith("Relay")) ?? d.jobs[0];
        if (!job) return toast(`${name} has no job to run`, "err");
        await api.post(`/jobs/${job.id}/run`);
        toast(`${job.name} started`);
      }
      if (a.type === "idea_status" && a.idea_id && a.status) {
        const idea = await api.get<{ title: string }>(`/ideas/${a.idea_id}`);
        if (!(await confirm(`Mark "${idea.title}" as ${a.status}?`))) return;
        await api.post(`/ideas/${a.idea_id}`, { status: a.status });
        toast(`Idea marked ${a.status}. Claudia will see it.`);
      }
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }, [confirm, nav, toast]);

  const ask = useCallback(async (text: string) => {
    if (busy.current || !text.trim()) return;
    busy.current = true;
    setHeard(text);
    setReply("");
    setErr("");
    setPhase("thinking");
    const pulse = window.setInterval(() => emitLevel("chief-assistant", 0.2 + Math.random() * 0.2), 120);
    try {
      const r = await api.post<{ say: string; action: Action | null }>("/ask", { text, page: loc.pathname });
      window.clearInterval(pulse);
      setReply(r.say);
      if (r.action) act(r.action);
      setPhase("speaking");
      await speak("chief-assistant", r.say);
    } catch (e) {
      window.clearInterval(pulse);
      setErr((e as Error).message);
    } finally {
      emitLevel(null, 0);
      busy.current = false;
      setPhase(wakeRef.current ? "listening" : "idle");
    }
  }, [act, loc.pathname]);

  const setWakeOn = useCallback((on: boolean) => {
    wakeRef.current?.stop();
    wakeRef.current = null;
    setWake(on);
    writePref(on);
    if (!on) return setPhase("idle");
    wakeRef.current = startWakeWord(
      (cmd) => ask(cmd),
      () => { setPhase("heard"); setHeard("Yes?"); },
      (s, msg) => {
        if (s === "error") { setErr(msg ?? "Microphone unavailable"); setWake(false); writePref(false); wakeRef.current = null; setPhase("idle"); }
        else if (s === "listening" && !busy.current) setPhase("listening");
      },
      (t) => setLive(t),
    );
  }, [ask]);

  // Restore the switch, and stop listening whenever the tab is hidden or closed.
  useEffect(() => {
    if (readPref() && canListen()) setWakeOn(true);
    const vis = () => {
      if (document.hidden) { wakeRef.current?.stop(); wakeRef.current = null; }
      else if (readPref() && !wakeRef.current) setWakeOn(true);
    };
    document.addEventListener("visibilitychange", vis);
    return () => { document.removeEventListener("visibilitychange", vis); wakeRef.current?.stop(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const pushToTalk = async () => {
    if (phase === "speaking") return stopSpeaking();
    if (busy.current) return;
    setErr("");
    setReply("");
    setHeard("");
    setLive("");
    setTapListening(true);
    try {
      setPhase("listening");
      const said = await listenOnce((t) => setLive(t));
      setTapListening(false);
      if (said) await ask(said);
      else {
        setErr("I did not hear anything. Tap the orb and speak straight away.");
        setPhase(wake ? "listening" : "idle");
      }
    } catch (e) {
      setTapListening(false);
      setErr((e as Error).message);
      setPhase("idle");
    }
  };

  const ring = { idle: "#4a6a8a", listening: "#29d9f5", heard: "#f4dca0", thinking: "#ff8a65", speaking: "#f4dca0" }[phase];
  const label = { idle: wake ? "say “Claudia”" : "tap to talk", listening: wake ? "listening for “Claudia”" : "listening…", heard: "yes?", thinking: "thinking…", speaking: "speaking · tap to stop" }[phase];
  const showBubble = tapListening || (phase !== "idle" && phase !== "listening") || !!err;

  return (
    <div className="relative flex items-center gap-2">
      <button onClick={pushToTalk} disabled={!canListen() && phase !== "speaking"}
              className="relative h-10 w-10 rounded-full border transition focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan"
              style={{ borderColor: ring, boxShadow: `0 0 18px ${ring}88, inset 0 0 12px ${ring}55` }}
              aria-label={`Claudia: ${label}`} title={canListen() ? `Claudia: ${label}` : "Voice needs Chrome or Edge"}>
        {(phase === "listening" || phase === "thinking" || phase === "speaking") && (
          <span className="absolute inset-0 rounded-full animate-pulseRing" style={{ background: `${ring}55` }} />
        )}
        <img src="/art/chief-assistant.webp" alt="" className="relative mx-auto h-8 w-8 rounded-full object-cover object-top mix-blend-screen" />
      </button>
      <div className="hidden leading-tight md:block">
        <div className="font-display text-[10px] tracking-[.3em]" style={{ color: "#f4dca0" }}>CLAUDIA</div>
        <div className="hud-label normal-case tracking-normal">{label}</div>
        {wake && live && phase === "listening" && <div className="max-w-[180px] truncate font-mono text-[10px] text-cyan/70">hearing: {live}</div>}
      </div>
      <label className="ml-1 flex cursor-pointer items-center gap-1.5" title="Listen for “Claudia” while HQ is open">
        <input id="wake-switch" type="checkbox" className="accent-cyan" checked={wake} disabled={!canListen()}
               onChange={(e) => setWakeOn(e.target.checked)} />
        <span className="hud-label">wake word</span>
      </label>

      {showBubble && (
        <div className="panel absolute right-0 top-12 z-50 w-[340px] p-3">
          {tapListening && (
            <div>
              <div className="font-display text-[10px] tracking-[.3em] text-cyan animate-flicker">LISTENING · SPEAK NOW</div>
              <div className="mt-1 min-h-[1.25rem] font-mono text-[12px] text-ink/80">{live || "…"}</div>
            </div>
          )}
          {heard && <div className="font-mono text-[11px] text-dim">you: “{heard}”</div>}
          {reply && <div className={clsx("mt-2 text-sm leading-relaxed", "text-ink")}>{reply}</div>}
          {phase === "thinking" && <div className="mt-2 font-display text-[10px] tracking-[.3em] text-coral animate-flicker">THINKING</div>}
          {err && <div className="mt-2 font-mono text-[11px] text-alert">{err}</div>}
          <div className="mt-2 flex justify-end gap-2">
            {phase === "speaking" && <button className="btn btn-ghost" onClick={stopSpeaking}>Stop</button>}
            <button className="btn btn-ghost" onClick={() => { setHeard(""); setReply(""); setErr(""); if (!busy.current) setPhase(wake ? "listening" : "idle"); }}>Close</button>
          </div>
        </div>
      )}
    </div>
  );
}
