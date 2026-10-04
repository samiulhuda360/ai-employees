import { useEffect, useState, type FormEvent } from "react";
import { api } from "../lib/api";

export default function Login({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState<"password" | "code">("password");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [demo, setDemo] = useState(false);
  const [demoCode, setDemoCode] = useState("");

  useEffect(() => {
    api.get<{ demo: boolean }>("/mode").then((m) => setDemo(m.demo)).catch(() => setDemo(false));
  }, []);

  const submitPassword = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await api.post<{ challenge: string; demo_code?: string }>("/login", { password });
      setChallenge(r.challenge);
      setDemoCode(r.demo_code ?? "");
      setStep("code");
      setPassword("");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await api.post("/verify", { challenge, code });
      onDone();
    } catch (e) {
      setErr((e as Error).message);
      if ((e as Error).message.includes("expired")) setStep("password");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full items-center justify-center p-4">
      <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
        <div className="absolute left-1/2 top-1/2 h-[520px] w-[520px] -translate-x-1/2 -translate-y-1/2 rounded-full border border-cyan/10" />
        <div className="absolute left-1/2 top-1/2 h-[380px] w-[380px] -translate-x-1/2 -translate-y-1/2 rounded-full border border-cyan/20" />
        <div className="absolute inset-x-0 h-24 bg-gradient-to-b from-transparent via-cyan/5 to-transparent animate-scan" />
      </div>
      <form onSubmit={step === "password" ? submitPassword : submitCode} className="panel relative w-full max-w-sm p-6">
        <div className="mb-6 text-center">
          <div className="font-display text-xl tracking-[.4em] text-ink glow-text">HERMES HQ</div>
          <div className="hud-label mt-2">{step === "password" ? "Identify yourself, commander" : demoCode ? "Demo mode: no Telegram, the code is below" : "Claudia sent a code to your Telegram"}</div>
        </div>
        {step === "password" ? (
          <label className="block">
            <span className="hud-label">Access key</span>
            <input id="hq-password" type="password" autoComplete="current-password" className="input mt-1" value={password}
                   onChange={(e) => setPassword(e.target.value)} autoFocus required />
          </label>
        ) : (
          <label className="block">
            <span className="hud-label">6-digit code</span>
            <input id="hq-code" inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}" maxLength={6}
                   className="input mt-1 text-center text-2xl tracking-[.6em]" value={code}
                   onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} autoFocus required />
          </label>
        )}
        {demo && step === "password" && <p className="mt-3 font-mono text-xs text-cyan">Demo mode: the access key is demo</p>}
        {demoCode && step === "code" && <p className="mt-3 font-mono text-xs text-cyan">Demo code: {demoCode}</p>}
        {err && <p className="mt-3 font-mono text-xs text-alert">{err}</p>}
        <button className="btn mt-5 w-full justify-center" disabled={busy}>
          {busy ? "Verifying…" : step === "password" ? "Request code" : "Enter the deck"}
        </button>
        {step === "code" && (
          <button type="button" className="btn btn-ghost mt-2 w-full justify-center" onClick={() => { setStep("password"); setCode(""); }}>
            Start over
          </button>
        )}
      </form>
    </div>
  );
}
