// Visual identity of every agent: colour, deck station, prop and voice.
// Positions are on the deck floor (x, z) around Claudia's command chair at the centre.

export interface AgentLook {
  id: string;
  name: string;
  color: string;
  station: string;
  pos: [number, number];
  prop: "clipboard" | "visor" | "tablet" | "stylus" | "dish" | "reticle" | "headset" | "hardhat" | "scanner" | "screens" | "mail";
  voice: { pitch: number; rate: number; prefer: string[] };
  tagline: string;
}

export const FLEET: AgentLook[] = [
  { id: "chief-assistant", name: "Claudia", color: "#f4dca0", station: "Command chair", pos: [0, 0], prop: "clipboard",
    voice: { pitch: 1.05, rate: 1.0, prefer: ["Aria", "Jenny", "Samantha", "Female", "Zira"] }, tagline: "Chief AI Assistant" },
  { id: "opportunity-scout", name: "Cash Builds", color: "#29d9f5", station: "Long-range scanners", pos: [-6, -3], prop: "visor",
    voice: { pitch: 0.9, rate: 1.05, prefer: ["Guy", "Daniel", "David", "Male"] }, tagline: "Tools you can ship in 5 days and sell" },
  { id: "growth-scout", name: "Growth Scout", color: "#29d9f5", station: "Long-range scanners", pos: [-4.8, 2.4], prop: "tablet",
    voice: { pitch: 1.0, rate: 1.0, prefer: ["Ryan", "George", "Male"] }, tagline: "Fernway growth research" },
  { id: "blog-planner", name: "Blog Planner", color: "#ff8a65", station: "Comms array", pos: [6, -3], prop: "stylus",
    voice: { pitch: 1.15, rate: 0.98, prefer: ["Sonia", "Libby", "Female"] }, tagline: "Easy-to-rank blog ideas" },
  { id: "social-planner", name: "Social Planner", color: "#ff8a65", station: "Comms array", pos: [6, 1.5], prop: "dish",
    voice: { pitch: 1.25, rate: 1.08, prefer: ["Natasha", "Emma", "Female"] }, tagline: "Post ideas for every platform" },
  { id: "prospect-finder", name: "Prospect Finder", color: "#f7b538", station: "Tactical", pos: [-2.6, 5.2], prop: "reticle",
    voice: { pitch: 0.85, rate: 1.02, prefer: ["Christopher", "Eric", "Male"] }, tagline: "Weak GBP, ready to help" },
  { id: "client-wins", name: "Client Wins", color: "#f7b538", station: "Tactical", pos: [2.6, 5.2], prop: "headset",
    voice: { pitch: 1.1, rate: 1.0, prefer: ["Michelle", "Ana", "Female"] }, tagline: "Customer health" },
  { id: "code-health", name: "Code Health", color: "#3ee6a8", station: "Remote uplink", pos: [0, -7.2], prop: "scanner",
    voice: { pitch: 0.8, rate: 0.95, prefer: ["Thomas", "Male"] }, tagline: "Repo security audit" },
  { id: "youtube-watcher", name: "YouTube Watcher", color: "#3ee6a8", station: "Remote uplink", pos: [3.2, -6.4], prop: "screens",
    voice: { pitch: 1.2, rate: 1.05, prefer: ["Jenny", "Female"] }, tagline: "Learns from videos" },
];

export const LOOK: Record<string, AgentLook> = Object.fromEntries(FLEET.map((a) => [a.id, a]));

export const STATE_COLOR: Record<string, string> = {
  ok: "#3ee6a8",
  late: "#f7b538",
  failed: "#ff4d6d",
  paused: "#7894b3",
  idle: "#4a6a8a",
};

export const TYPE_LABEL: Record<string, string> = {
  saas: "SaaS idea",
  cash_build: "Cash build",
  feature: "Product feature",
  quick_win: "Quick win",
  blog: "Blog post",
  social: "Social post",
  prospect: "Prospect",
  customer_win: "Customer win",
  customer_risk: "Customer at risk",
  customer_lead: "Not yet paying",
  code_fix: "Code fix",
  agent_idea: "New agent",
  upgrade: "Agent upgrade",
  learning: "Learning",
  proposal: "Proposed rule",
};

export const TYPE_COLOR: Record<string, string> = {
  saas: "#29d9f5",
  cash_build: "#f4dca0",
  feature: "#29d9f5",
  quick_win: "#3ee6a8",
  blog: "#ff8a65",
  social: "#ff8a65",
  prospect: "#f7b538",
  customer_win: "#3ee6a8",
  customer_risk: "#ff4d6d",
  customer_lead: "#f7b538",
  job: "#3ee6a8",
  code_fix: "#ff4d6d",
  agent_idea: "#f4dca0",
  upgrade: "#f4dca0",
  learning: "#3ee6a8",
  proposal: "#f4dca0",
};
