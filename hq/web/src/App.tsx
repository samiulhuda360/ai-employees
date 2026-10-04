import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import Shell from "./components/Shell";
import { Loading, UiProvider } from "./components/ui";
import { api, type Overview } from "./lib/api";
import { AgentStation, AgentsList } from "./pages/Agents";
import Deck from "./pages/Deck";
import Board from "./pages/Board";
import Ideas from "./pages/Ideas";
import Login from "./pages/Login";
import { Customers, Log, Me, Prospects } from "./pages/Other";

type Auth = "checking" | "in" | "out";

export default function App() {
  const [auth, setAuth] = useState<Auth>("checking");
  const [overview, setOverview] = useState<Overview | null>(null);

  const load = useCallback(() => {
    api.get<Overview>("/overview").then(setOverview).catch(() => {});
  }, []);

  useEffect(() => {
    api.get("/me").then(() => setAuth("in")).catch(() => setAuth("out"));
    const out = () => { setAuth("out"); setOverview(null); };
    window.addEventListener("hq:logout", out);
    return () => window.removeEventListener("hq:logout", out);
  }, []);

  // Live: refresh the fleet picture every 30 seconds while logged in.
  useEffect(() => {
    if (auth !== "in") return;
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [auth, load]);

  return (
    <UiProvider>
      {auth === "checking" ? <Loading /> : auth === "out" ? <Login onDone={() => setAuth("in")} /> : (
        <BrowserRouter>
          <Shell overview={overview} onFleetChange={load}>
            <Routes>
              <Route path="/" element={<Deck data={overview} reload={load} />} />
              <Route path="/agents" element={<AgentsList data={overview} />} />
              <Route path="/agents/:id" element={<AgentStation onChanged={load} />} />
              <Route path="/ideas" element={<Ideas />} />
              <Route path="/build" element={<Board kind="build" />} />
              <Route path="/write" element={<Board kind="write" />} />
              <Route path="/prospects" element={<Prospects />} />
              <Route path="/customers" element={<Customers />} />
              <Route path="/me" element={<Me />} />
              <Route path="/log" element={<Log />} />
              <Route path="*" element={<Deck data={overview} reload={load} />} />
            </Routes>
          </Shell>
        </BrowserRouter>
      )}
    </UiProvider>
  );
}
