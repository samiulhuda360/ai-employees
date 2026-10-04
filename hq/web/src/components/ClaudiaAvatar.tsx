// Claudia as a real-time 3D person (TalkingHead, MIT). She pops up when she is thinking
// or speaking, with lip-sync to her Kokoro voice, blinking, eye contact and idle motion.
// TalkingHead is loaded from /talkinghead as a plain ES module (see index.html import map)
// only the first time she appears; the avatar file is cached by the browser afterwards.
//
// Lip-sync: TalkingHead turns words into mouth shapes. Kokoro does not return word timings,
// so each word gets a slice of the sentence's audio in proportion to its length, which
// keeps the mouth in step for normal speech.

import clsx from "clsx";
import { useCallback, useEffect, useRef, useState } from "react";
import { setAvatarSpeaker, type Clip } from "../lib/voice";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Head = any;

const AVATAR_URL = "/avatars/claudia.glb";
const TALKINGHEAD_URL = "/talkinghead/talkinghead.mjs"; // served from public/, not bundled
const PIN = "hq.avatar.pinned";
const readPin = () => { try { return localStorage.getItem(PIN) === "1"; } catch { return false; } };
const writePin = (on: boolean) => { try { localStorage.setItem(PIN, on ? "1" : "0"); } catch { /* private mode */ } };

function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(what)), ms);
    p.then((v) => { clearTimeout(t); resolve(v); }, (e) => { clearTimeout(t); reject(e); });
  });
}

function wordTimings(text: string, durationMs: number) {
  const words = text.split(/\s+/).filter(Boolean);
  const weights = words.map((w) => Math.max(2, w.replace(/[^\p{L}\p{N}]/gu, "").length) + 1);
  const sum = weights.reduce((a, b) => a + b, 0) || 1;
  const lead = 60;
  const usable = Math.max(200, durationMs - 160);
  const wtimes: number[] = [];
  const wdurations: number[] = [];
  let t = lead;
  for (const w of weights) {
    const d = (usable * w) / sum;
    wtimes.push(Math.round(t));
    wdurations.push(Math.round(d * 0.92));
    t += d;
  }
  return { words, wtimes, wdurations };
}

export default function ClaudiaAvatar() {
  const node = useRef<HTMLDivElement>(null);
  const head = useRef<Head | null>(null);
  const loading = useRef(false);
  const [phase, setPhase] = useState("idle");
  const [pinned, setPinned] = useState(readPin());
  const [visible, setVisible] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [failed, setFailed] = useState("");
  const hideTimer = useRef<number | undefined>(undefined);

  const ensureHead = useCallback(async () => {
    if (head.current || loading.current || !node.current) return;
    loading.current = true;
    setFailed("");
    setProgress(0);
    let blobUrl = "";
    try {
      // Download the avatar ourselves so progress is real and a stall is visible.
      const res = await fetch(AVATAR_URL, { cache: "force-cache" });
      if (res.status === 404) throw new Error("no avatar file yet (add public/avatars/claudia.glb, see the README)");
      if (!res.ok || !res.body) throw new Error(`avatar download HTTP ${res.status}`);
      const total = Number(res.headers.get("content-length")) || 0;
      const reader = res.body.getReader();
      const chunks: Uint8Array[] = [];
      let got = 0;
      for (;;) {
        const { done, value } = await withTimeout(reader.read(), 30000, "avatar download stalled");
        if (done) break;
        chunks.push(value);
        got += value.length;
        if (total) setProgress(Math.min(99, Math.round((got / total) * 100)));
      }
      blobUrl = URL.createObjectURL(new Blob(chunks as BlobPart[], { type: "model/gltf-binary" }));
      const mod: any = await withTimeout(import(/* @vite-ignore */ TALKINGHEAD_URL), 30000, "avatar engine did not load");
      node.current.innerHTML = ""; // clean start on retry
      const h = new mod.TalkingHead(node.current, {
        ttsEndpoint: null,
        lipsyncModules: ["en"],
        lipsyncLang: "en",
        cameraView: "upper",
        cameraX: 0,
        cameraY: 0.3, // camera at eye level so she looks at you, not down at you
        cameraDistance: -0.3,
        cameraRotateEnable: false,
        cameraZoomEnable: false,
        cameraPanEnable: false,
        modelFPS: 30,
        avatarMood: "neutral",
        avatarIdleEyeContact: 0.7,
        avatarSpeakingEyeContact: 1, // hold eye contact while talking
        avatarIdleHeadMove: 0.3,
        avatarSpeakingHeadMove: 0.3, // calmer nods, so her gaze does not wander down
        lightAmbientIntensity: 2.2,
        lightDirectIntensity: 28,
        lightDirectColor: 0xfff1d6,
        lightSpotIntensity: 0,
      });
      // TalkingHead's glances mostly aim down (eyesRotateX up to 0.6), which drags the eyelids
      // and reads as drowsy. Keep her glances small and level.
      for (const mode of ["idle", "speaking"]) {
        for (const alt of h.animTemplateEyes?.[mode]?.alt ?? []) {
          const vs = alt.vs ?? {};
          if (vs.eyesRotateX) vs.eyesRotateX = vs.eyesRotateX.map((v: unknown) => (Array.isArray(v) ? [-0.08, 0.08] : v));
          if (vs.eyesRotateY) vs.eyesRotateY = vs.eyesRotateY.map((v: unknown) => (Array.isArray(v) ? [-0.3, 0.3] : v));
        }
      }
      setProgress(100);
      await withTimeout(h.showAvatar({
        url: blobUrl,
        body: "F",
        avatarMood: "neutral",
        lipsyncLang: "en",
      }), 40000, "building the avatar took too long");
      head.current = h;
      if (import.meta.env.DEV || location.port === "8766") (window as any).__claudia = h; // test hook
      setProgress(null);
      setAvatarSpeaker({
        async speak(clip: Clip, text: string) {
          const ctx: AudioContext = h.audioCtx;
          if (ctx.state === "suspended") await ctx.resume();
          const audio = await ctx.decodeAudioData(clip.buf.slice(0));
          const ms = audio.duration * 1000;
          h.lookAtCamera(300);
          // Neural voices report where each word starts; Kokoro does not, so estimate.
          h.speakAudio({ audio, ...(clip.timing && clip.timing.words.length ? clip.timing : wordTimings(text, ms)) });
          // Resolve when she has finished this sentence.
          const started = performance.now();
          await new Promise<void>((resolve) => {
            const check = () => {
              const elapsed = performance.now() - started;
              if ((elapsed > 250 && !h.isSpeaking && !h.isAudioPlaying) || elapsed > ms + 2500) resolve();
              else setTimeout(check, 80);
            };
            setTimeout(check, 120);
          });
        },
        stop() {
          try { h.stopSpeaking(); } catch { /* not speaking */ }
        },
      });
    } catch (e) {
      console.error("Claudia avatar:", e);
      setFailed(`Avatar did not load: ${(e as Error).message}. She still speaks without it.`);
      setProgress(null);
      setAvatarSpeaker(null);
    } finally {
      if (blobUrl) setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
      loading.current = false;
    }
  }, []);

  const retry = () => { head.current = null; ensureHead(); };

  // Follow Claudia's phase from the orb.
  useEffect(() => {
    const on = (e: Event) => setPhase((e as CustomEvent<string>).detail);
    window.addEventListener("claudia:phase", on);
    return () => window.removeEventListener("claudia:phase", on);
  }, []);

  useEffect(() => {
    const active = phase === "thinking" || phase === "speaking" || phase === "heard";
    window.clearTimeout(hideTimer.current);
    if (active || pinned) {
      setVisible(true);
      ensureHead();
    } else if (visible) {
      hideTimer.current = window.setTimeout(() => setVisible(false), 5000);
    }
    const h = head.current;
    if (h) {
      try {
        if (phase === "thinking") { h.setMood("neutral"); h.lookAtCamera(400); }
        if (phase === "heard") { h.lookAtCamera(600); h.setMood("happy"); }
        if (phase === "speaking") { h.setMood("neutral"); h.lookAtCamera(400); }
        if (phase === "idle" || phase === "listening") h.setMood("neutral");
      } catch { /* gesture not in this build: ignore */ }
    }
  }, [phase, pinned]); // eslint-disable-line react-hooks/exhaustive-deps

  // Pause rendering while hidden to save the GPU.
  useEffect(() => {
    const h = head.current;
    if (!h) return;
    try { if (visible) h.start(); else h.stop(); } catch { /* older builds */ }
  }, [visible]);

  // The deck pauses only while she is thinking or speaking, so lip-sync gets the GPU.
  // Pinned and standing by, the deck keeps running behind her.
  useEffect(() => {
    const busy = visible && (phase === "thinking" || phase === "speaking");
    window.dispatchEvent(new CustomEvent("claudia:avatar", { detail: busy }));
  }, [visible, phase]);

  useEffect(() => () => setAvatarSpeaker(null), []);

  const togglePin = () => { const v = !pinned; setPinned(v); writePin(v); };

  return (
    <>
      <div className={clsx("panel fixed bottom-20 right-4 z-30 w-[300px] overflow-hidden transition-all duration-500 sm:w-[340px]",
                           visible ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-6 opacity-0")}
           aria-hidden={!visible}>
        <div className="flex items-center justify-between border-b border-line px-3 py-2">
          <span className="font-display text-[10px] tracking-[.35em]" style={{ color: "#f4dca0" }}>CLAUDIA · LIVE</span>
          <div className="flex items-center gap-2">
            <span className="hud-label">{phase === "thinking" ? "thinking" : phase === "speaking" ? "speaking" : "standing by"}</span>
            <button className="btn btn-ghost px-2 py-0.5" onClick={togglePin} title={pinned ? "Unpin: hide when she is quiet" : "Pin: keep her on screen"}>
              {pinned ? "Unpin" : "Pin"}
            </button>
          </div>
        </div>
        <div className="relative h-[360px] sm:h-[400px]" style={{ background: "radial-gradient(circle at 50% 35%, #1a2a3f 0%, #060d1a 70%)" }}>
          <div ref={node} className="absolute inset-0" />
          {progress !== null && (
            <div className="absolute inset-x-6 bottom-6">
              <div className="hud-label mb-1">materialising claudia · {progress}%</div>
              <div className="h-1 bg-line"><div className="h-1 bg-cyan transition-all" style={{ width: `${progress}%`, boxShadow: "0 0 10px #29d9f5" }} /></div>
            </div>
          )}
          {failed && (
            <div className="absolute inset-x-4 bottom-4">
              <div className="font-mono text-[11px] text-amber">{failed}</div>
              <button className="btn btn-amber mt-2" onClick={retry}>Retry</button>
            </div>
          )}
          <div className="pointer-events-none absolute inset-0" style={{ background: "repeating-linear-gradient(to bottom, rgba(41,217,245,.035) 0 1px, transparent 1px 3px)" }} />
        </div>
      </div>
      {!visible && !pinned && (
        <button className="btn fixed right-4 top-[6.5rem] z-30" onClick={togglePin} title="Show Claudia">Show Claudia</button>
      )}
    </>
  );
}
