// Voices and listening for Hermes HQ, at zero API cost.
//   Speaking:  Kokoro on the HQ server (/api/tts, British voices), played sentence by
//              sentence so Claudia starts talking within about a second. Falls back to the
//              browser's own speech if the server voice fails.
//   Listening: the browser's SpeechRecognition (Chrome/Edge). "Wake word" mode listens
//              continuously for "Claudia" while HQ is open and the switch is on.
// A small event bus lets the 3D deck animate whichever agent is speaking or thinking.

import { LOOK } from "./fleet";

type Listener = (agentId: string | null, level: number) => void;
const listeners = new Set<Listener>();
export function onSpeaking(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}
export function emitLevel(id: string | null, level: number) {
  listeners.forEach((fn) => fn(id, level));
}

// ---------------------------------------------------------------- speaking

// When Claudia's 3D avatar is on screen it plays her voice itself, so the lips follow it.
export interface WordTiming { words: string[]; wtimes: number[]; wdurations: number[] }
export interface Clip { buf: ArrayBuffer; type: string; timing: WordTiming | null }
export interface AvatarSpeaker {
  speak(clip: Clip, text: string): Promise<void>;
  stop(): void;
}
let avatar: AvatarSpeaker | null = null;
export function setAvatarSpeaker(a: AvatarSpeaker | null) {
  avatar = a;
}

let current: HTMLAudioElement | null = null;
let queueToken = 0;
let ctx: AudioContext | null = null;
let speakingNow = false;
export const isSpeaking = () => speakingNow;

function sentences(text: string): string[] {
  const parts = text.replace(/\s+/g, " ").match(/[^.!?]+[.!?]+["')\]]*|[^.!?]+$/g) ?? [text];
  // Merge very short fragments so each request carries a natural phrase.
  const out: string[] = [];
  for (const p of parts.map((s) => s.trim()).filter(Boolean)) {
    if (out.length && (out[out.length - 1].length < 40 || p.length < 12)) out[out.length - 1] += " " + p;
    else out.push(p);
  }
  return out;
}

// The server answers with mp3 (neural voice, with word timings in x-words) or wav (Kokoro fallback).
async function fetchAudio(agentId: string, text: string): Promise<Clip> {
  const res = await fetch("/api/tts", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "x-hq": "1" },
    body: JSON.stringify({ agent: agentId, text }),
  });
  if (!res.ok) throw new Error(`voice ${res.status}`);
  let timing: WordTiming | null = null;
  try {
    const h = res.headers.get("x-words");
    if (h) timing = JSON.parse(h) as WordTiming;
  } catch { /* no timings: the avatar estimates them */ }
  return { buf: await res.arrayBuffer(), type: res.headers.get("content-type") || "audio/mpeg", timing };
}

async function fetchClip(agentId: string, text: string): Promise<string> {
  const c = await fetchAudio(agentId, text);
  return URL.createObjectURL(new Blob([c.buf], { type: c.type }));
}

function playClip(agentId: string, url: string, token: number): Promise<void> {
  return new Promise((resolve) => {
    if (token !== queueToken) return resolve();
    const audio = new Audio(url);
    current = audio;
    let raf = 0;
    try {
      ctx = ctx ?? new AudioContext();
      const src = ctx.createMediaElementSource(audio);
      const an = ctx.createAnalyser();
      an.fftSize = 256;
      src.connect(an);
      an.connect(ctx.destination);
      const buf = new Uint8Array(an.frequencyBinCount);
      const tick = () => {
        an.getByteTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) sum += Math.abs(v - 128);
        emitLevel(agentId, Math.min(1, (sum / buf.length) / 18));
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    } catch { /* analyser not available: still plays */ }
    const done = () => {
      cancelAnimationFrame(raf);
      URL.revokeObjectURL(url);
      resolve();
    };
    audio.onended = done;
    audio.onerror = done;
    audio.play().catch(done);
  });
}

function browserSpeak(agentId: string, text: string): Promise<void> {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window)) return resolve();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-GB";
    const look = LOOK[agentId];
    u.pitch = look?.voice.pitch ?? 1;
    u.rate = look?.voice.rate ?? 1;
    u.onboundary = () => emitLevel(agentId, 1);
    u.onend = () => resolve();
    u.onerror = () => resolve();
    window.speechSynthesis.speak(u);
  });
}

export function canSpeak() {
  return typeof window !== "undefined" && typeof Audio !== "undefined";
}

export function stopSpeaking() {
  queueToken++;
  avatar?.stop();
  current?.pause();
  current = null;
  window.speechSynthesis?.cancel();
  speakingNow = false;
  emitLevel(null, 0);
}

// Speak as an agent in its own server voice. Next sentence is fetched while the current plays.
export async function speak(agentId: string, text: string): Promise<void> {
  stopSpeaking();
  const token = ++queueToken;
  const parts = sentences(text).slice(0, 12);
  if (!parts.length) return;
  speakingNow = true;
  // Claudia with her avatar on screen: the avatar plays each sentence with lip-sync.
  if (agentId === "chief-assistant" && avatar) {
    const a = avatar;
    const pulse = window.setInterval(() => emitLevel(agentId, 0.3 + Math.random() * 0.6), 110);
    try {
      let next = fetchAudio(agentId, parts[0]);
      for (let i = 0; i < parts.length; i++) {
        const clip = await next;
        if (i + 1 < parts.length) next = fetchAudio(agentId, parts[i + 1]);
        if (token !== queueToken) return;
        await a.speak(clip, parts[i]);
        if (token !== queueToken) return;
      }
      return;
    } catch {
      /* avatar failed: fall through to plain audio */
    } finally {
      window.clearInterval(pulse);
      if (token === queueToken) {
        speakingNow = false;
        emitLevel(null, 0);
      }
    }
    if (token !== queueToken) return;
    speakingNow = true;
  }
  try {
    let next = fetchClip(agentId, parts[0]);
    for (let i = 0; i < parts.length; i++) {
      const url = await next;
      if (i + 1 < parts.length) next = fetchClip(agentId, parts[i + 1]);
      await playClip(agentId, url, token);
      if (token !== queueToken) return;
    }
  } catch {
    if (token === queueToken) await browserSpeak(agentId, parts.join(" "));
  } finally {
    if (token === queueToken) {
      speakingNow = false;
      emitLevel(null, 0);
    }
  }
}

// ---------------------------------------------------------------- listening

interface RecResult { isFinal: boolean; 0: { transcript: string } }
interface RecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((e: { resultIndex: number; results: ArrayLike<RecResult> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
}

function Recognition(): (new () => RecognitionLike) | null {
  const w = window as unknown as Record<string, new () => RecognitionLike>;
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function canListen() {
  return Boolean(Recognition());
}

// Plain-language reasons for the recogniser's error codes.
export function micError(code: string): string {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone is blocked. Click “Not secure” (left of the address bar) → Site settings → Microphone → Allow, then reload.";
    case "no-speech":
      return "I did not hear anything. Tap the orb and speak straight away.";
    case "audio-capture":
      return "No microphone found. Plug one in or pick it in Windows sound settings.";
    case "network":
      return "The browser's speech service is unreachable. Check the internet connection.";
    case "aborted":
      return "";
    default:
      return `Microphone error: ${code}`;
  }
}

// Listen for one utterance. onInterim shows words as they are recognised.
export function listenOnce(onInterim?: (text: string) => void): Promise<string> {
  return new Promise((resolve, reject) => {
    const Ctor = Recognition();
    if (!Ctor) return reject(new Error("This browser cannot listen. Use Chrome or Edge."));
    const rec = new Ctor();
    rec.lang = navigator.language || "en-US";
    rec.continuous = false;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    let got = "";
    let failed = false;
    rec.onresult = (e) => {
      let text = "";
      for (let i = 0; i < e.results.length; i++) text += e.results[i][0].transcript;
      got = text.trim();
      onInterim?.(got);
    };
    rec.onerror = (e) => {
      failed = true;
      const msg = micError(e.error);
      if (msg) reject(new Error(msg));
      else resolve(got);
    };
    rec.onend = () => { if (!failed) resolve(got); };
    try { rec.start(); } catch (err) { reject(err as Error); }
  });
}

// Wake word: "Claudia" (and the ways speech recognition mishears it).
const WAKE = /\b(claudia|clodia|claudio|cloudia|claudiya|klaudia|gladia|claudius)\b[,.!?\s]*/i;

export interface WakeHandle { stop(): void }

// Listen continuously. Calls onCommand with whatever followed "Claudia" in the same
// utterance; if nothing followed, calls onWake and the next utterance becomes the command.
export function startWakeWord(onCommand: (text: string) => void, onWake: () => void, onState: (s: "listening" | "off" | "error", msg?: string) => void, onHeard?: (text: string) => void): WakeHandle {
  const Ctor = Recognition();
  if (!Ctor) {
    onState("error", "This browser cannot listen. Use Chrome or Edge.");
    return { stop() {} };
  }
  let stopped = false;
  let armed = false; // heard "Claudia" alone; next final utterance is the command
  let rec: RecognitionLike | null = null;

  const begin = () => {
    if (stopped) return;
    rec = new Ctor();
    rec.lang = navigator.language || "en-US";
    rec.continuous = true;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    rec.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (!speakingNow) onHeard?.(r[0].transcript.trim()); // proves the mic works
        if (!r.isFinal) continue;
        const text = r[0].transcript.trim();
        if (speakingNow) continue; // never react to Claudia's own voice
        if (armed) {
          armed = false;
          if (text) onCommand(text);
          continue;
        }
        const m = WAKE.exec(text);
        if (!m) continue;
        const rest = text.slice(m.index + m[0].length).trim();
        if (rest.length > 2) onCommand(rest);
        else {
          armed = true;
          onWake();
        }
      }
    };
    rec.onerror = (e) => {
      if (["not-allowed", "service-not-allowed", "audio-capture"].includes(e.error)) {
        stopped = true;
        onState("error", micError(e.error));
      }
    };
    rec.onend = () => {
      // Chrome ends continuous sessions after silence; restart while the switch is on.
      if (!stopped) setTimeout(begin, 250);
      else onState("off");
    };
    try {
      rec.start();
      onState("listening");
    } catch { setTimeout(begin, 1000); }
  };
  begin();
  return {
    stop() {
      stopped = true;
      try { rec?.abort(); } catch { /* already stopped */ }
      onState("off");
    },
  };
}
