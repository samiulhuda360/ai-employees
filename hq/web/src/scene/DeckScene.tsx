// The command deck: every agent as a hologram at its station, driven by live state.
//   ok      bright, slow breathing, orbiting ring
//   late    amber pulse
//   failed  red flicker
//   paused  dim outline, no orbit
//   quiet hours (23:00-07:00)  everyone powers down except a faint outline
// A data packet flies to the Relay beam when an agent delivered in the last two hours,
// and whoever is speaking (voice.ts) has their head pulse with the words.

import { Canvas, useFrame } from "@react-three/fiber";
import { Billboard, Edges, Environment, Html, Lightformer, MeshReflectorMaterial, OrbitControls, PerformanceMonitor, Stars, useGLTF, useTexture } from "@react-three/drei";
import { Bloom, ChromaticAberration, EffectComposer, N8AO, Noise, Scanline, Vignette } from "@react-three/postprocessing";
import { BlendFunction } from "postprocessing";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { AgentSummary } from "../lib/api";
import { FLEET, LOOK, STATE_COLOR } from "../lib/fleet";
import { onSpeaking } from "../lib/voice";

const RELAY: [number, number, number] = [10.5, 0, -8.5];

// Agents with generated hologram art (gpt-image-2 on the ChatGPT plan, black background).
const HAS_ART = new Set([
  "chief-assistant", "opportunity-scout", "growth-scout", "blog-planner", "social-planner",
  "prospect-finder", "client-wins", "code-health", "youtube-watcher",
]);

interface Props {
  agents: AgentSummary[];
  quiet: boolean;
  selected: string | null;
  onSelect: (id: string) => void;
  paused?: boolean; // Claudia's avatar is on screen: stop rendering so she gets the GPU
}

// Quality: "high" = reflective floor, ambient occlusion, shadows; "low" = plain floor.
// Starts high (or what worked last time) and drops to low for good in this browser if the
// frame rate sags. All of it runs on the viewer's graphics card, not the server.
// ?quality=low|high in the URL overrides it.
function initialQuality(): "high" | "low" {
  const q = new URLSearchParams(location.search).get("quality");
  if (q === "low" || q === "high") return q;
  try { return localStorage.getItem("hq.deck.quality") === "low" ? "low" : "high"; } catch { return "high"; }
}

export default function DeckScene({ agents, quiet, selected, onSelect, paused }: Props) {
  const [quality, setQuality] = useState<"high" | "low">(initialQuality);
  const hq = quality === "high";
  const degrade = () => {
    setQuality("low");
    try { localStorage.setItem("hq.deck.quality", "low"); } catch { /* private mode */ }
  };
  const byId = useMemo(() => Object.fromEntries(agents.map((a) => [a.id, a])), [agents]);
  const [speaking, setSpeaking] = useState<{ id: string | null; level: number }>({ id: null, level: 0 });
  useEffect(() => onSpeaking((id, level) => setSpeaking({ id, level })), []);
  const reduce = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  return (
    <Canvas shadows camera={{ position: [0, 9.5, 15.5], fov: 42 }} dpr={hq ? [1, 1.5] : 1} gl={{ antialias: true }} frameloop={paused ? "demand" : "always"}>
      <PerformanceMonitor onDecline={degrade} flipflops={1} />
      <color attach="background" args={["#02050b"]} />
      <fog attach="fog" args={["#02050b", 18, 42]} />
      {/* Real light so surfaces read as solid: sky/ground fill, one shadow-casting key
          light from above-front, a warm light over Claudia's dais. */}
      <hemisphereLight args={["#5b7ea3", "#06090e", quiet ? 0.25 : 0.7]} />
      <ambientLight intensity={0.12} />
      <directionalLight position={[7, 15, 9]} intensity={quiet ? 0.5 : 1.6} color="#dbe8ff" castShadow={hq}
                        shadow-mapSize={[1024, 1024]} shadow-bias={-0.0004} shadow-normalBias={0.02}
                        shadow-camera-left={-15} shadow-camera-right={15} shadow-camera-top={15} shadow-camera-bottom={-15}
                        shadow-camera-near={1} shadow-camera-far={45} />
      <pointLight position={[0, 2.6, 0]} intensity={quiet ? 3 : 7} color="#f4dca0" distance={6} decay={2} />
      <pointLight position={[0, 6, -9]} intensity={quiet ? 5 : 18} color="#29d9f5" distance={14} decay={2} />
      <Stars radius={80} depth={40} count={2500} factor={3} saturation={0} fade speed={reduce ? 0 : 0.4} />

      {/* Procedural reflections: the deck's metal needs something to reflect; no downloads. */}
      <Environment resolution={128} frames={1}>
        <Lightformer form="ring" intensity={2.2} color="#29d9f5" scale={14} position={[0, 9, 0]} rotation-x={Math.PI / 2} />
        <Lightformer form="rect" intensity={1.4} color="#ff8a65" scale={[18, 2, 1]} position={[0, 3, -16]} />
        <Lightformer form="rect" intensity={0.8} color="#f4dca0" scale={[6, 6, 1]} position={[10, 6, 10]} rotation-y={-Math.PI / 4} />
      </Environment>
      <ReflectiveFloor quiet={quiet} mirror={hq} />
      <Suspense fallback={<DeckFloor />}>
        <DeckModel quiet={quiet} />
      </Suspense>
      <DeckFloor modelled />
      <CommandScreen agents={agents} quiet={quiet} />
      <RelayBeam />

      {FLEET.map((look) => {
        const a = byId[look.id];
        if (!a) return null;
        const talking = speaking.id === look.id ? speaking.level : 0;
        return (
          <Station key={look.id} agent={a} quiet={quiet} selected={selected === look.id}
                   talking={talking} onSelect={onSelect} reduce={reduce} />
        );
      })}

      <OrbitControls enablePan={false} minDistance={9} maxDistance={26} maxPolarAngle={Math.PI / 2.25}
                     autoRotate={!reduce && !selected} autoRotateSpeed={0.25} target={[0, 0.8, 0]} />
      <EffectComposer multisampling={0}>
        {hq ? <N8AO aoRadius={1.4} intensity={2.4} distanceFalloff={0.6} quality="medium" halfRes /> : <></>}
        <Bloom intensity={0.85} luminanceThreshold={0.55} luminanceSmoothing={0.25} mipmapBlur />
        <ChromaticAberration offset={new THREE.Vector2(0.0007, 0.0007)} radialModulation={false} modulationOffset={0} />
        <Scanline density={1.1} opacity={0.035} blendFunction={BlendFunction.OVERLAY} />
        <Noise opacity={0.035} />
        <Vignette eskil={false} offset={0.2} darkness={0.85} />
      </EffectComposer>
    </Canvas>
  );
}

// ---------------------------------------------------------------- floor, screen, relay

// Glossy deck plating: a tiled floor that reflects the holograms, consoles and light
// strips (blurred, like polished metal), and catches the key light's shadows. The tile
// pattern is drawn once on a canvas, so nothing is downloaded.
function tileTexture(): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = c.height = 512;
  const g = c.getContext("2d")!;
  g.fillStyle = "#8c8c8c";
  g.fillRect(0, 0, 512, 512);
  for (let i = 0; i < 4; i++) {
    for (let j = 0; j < 4; j++) {
      const v = 120 + ((i * 7 + j * 13) % 5) * 12; // slight per-plate variation
      g.fillStyle = `rgb(${v},${v},${v})`;
      g.fillRect(i * 128 + 3, j * 128 + 3, 122, 122);
    }
  }
  g.fillStyle = "#2a2a2a"; // grout lines between plates
  for (let k = 0; k <= 4; k++) {
    g.fillRect(k * 128 - 2, 0, 4, 512);
    g.fillRect(0, k * 128 - 2, 512, 4);
  }
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(7, 7);
  t.anisotropy = 8;
  return t;
}

function ReflectiveFloor({ quiet, mirror }: { quiet: boolean; mirror: boolean }) {
  const tex = useMemo(tileTexture, []);
  if (!mirror) {
    return (
      <mesh rotation-x={-Math.PI / 2} position={[0, 0.004, 0]}>
        <circleGeometry args={[12.36, 96]} />
        <meshStandardMaterial color="#3a4656" metalness={0.55} roughness={0.6} roughnessMap={tex} map={tex} />
      </mesh>
    );
  }
  return (
    <mesh rotation-x={-Math.PI / 2} position={[0, 0.004, 0]} receiveShadow>
      <circleGeometry args={[12.36, 128]} />
      <MeshReflectorMaterial resolution={512} mirror={0.35} blur={[400, 120]} mixBlur={1} mixStrength={quiet ? 0.6 : 1.5}
                             depthScale={0.6} minDepthThreshold={0.7} maxDepthThreshold={1.3}
                             color="#3a4656" metalness={0.55} roughness={0.72} roughnessMap={tex} map={tex} />
    </mesh>
  );
}

// The bridge built in Blender (hq/blender/build_deck.py -> public/models/deck.glb):
// floor, dais, curved back wall, status-board frame, pillars, truss, relay base.
// Quiet hours dim every light strip in it.
function DeckModel({ quiet }: { quiet: boolean }) {
  const { scene } = useGLTF("/models/deck.glb");
  const model = useMemo(() => {
    const s = scene.clone(true);
    s.traverse((o) => {
      const m = (o as THREE.Mesh).material as THREE.MeshStandardMaterial | undefined;
      if (m && !m.name.endsWith("Glow")) { o.castShadow = true; o.receiveShadow = true; }
      if (m && "emissiveIntensity" in m) {
        (o as THREE.Mesh).material = m.clone();
        const mm = (o as THREE.Mesh).material as THREE.MeshStandardMaterial;
        mm.userData.base = mm.emissiveIntensity;
        mm.envMapIntensity = 0.9;
      }
    });
    return s;
  }, [scene]);
  useEffect(() => {
    model.traverse((o) => {
      const m = (o as THREE.Mesh).material as THREE.MeshStandardMaterial | undefined;
      if (m?.userData?.base !== undefined) m.emissiveIntensity = m.userData.base * (quiet ? 0.25 : 1);
    });
  }, [model, quiet]);
  return <primitive object={model} />;
}

// The animated part of the floor (a slowly turning arc around the dais). Without the
// model (still loading, or failed), it also draws the old grid so the deck is never empty.
function DeckFloor({ modelled = false }: { modelled?: boolean }) {
  const ring = useRef<THREE.Mesh>(null);
  useFrame((_, dt) => {
    if (ring.current) ring.current.rotation.z += dt * 0.05;
  });
  return (
    <group>
      {!modelled && <gridHelper args={[60, 60, "#0e3a55", "#0a2236"]} position={[0, 0, 0]} />}
      <mesh ref={ring} rotation-x={-Math.PI / 2} position={[0, 0.34, 0]}>
        <ringGeometry args={[1.62, 1.72, 96, 1, 0, Math.PI * 1.6]} />
        <meshBasicMaterial color="#29d9f5" transparent opacity={0.9} toneMapped={false} />
      </mesh>
    </group>
  );
}

// One agent station from pedestal.glb; its glow ring and console screen take the
// agent's live colour (state colour when late/failed, dim when paused or quiet).
function Pedestal({ color, power }: { color: THREE.Color; power: number }) {
  const { scene } = useGLTF("/models/pedestal.glb");
  const model = useMemo(() => {
    const s = scene.clone(true);
    s.traverse((o) => {
      const mesh = o as THREE.Mesh;
      const m = mesh.material as THREE.MeshStandardMaterial | undefined;
      if (m && (m.name === "StationGlow" || m.name === "StationScreen")) mesh.material = m.clone();
      else if (m) { mesh.castShadow = true; mesh.receiveShadow = true; }
    });
    return s;
  }, [scene]);
  useEffect(() => {
    model.traverse((o) => {
      const m = (o as THREE.Mesh).material as THREE.MeshStandardMaterial | undefined;
      if (!m) return;
      if (m.name === "StationGlow") { m.color.copy(color); m.emissive.copy(color); m.emissiveIntensity = 2.5 * power; }
      if (m.name === "StationScreen") { m.color.copy(color); m.emissive.copy(color); m.emissiveIntensity = 1.1 * power; }
    });
  }, [model, color, power]);
  return (
    <>
      <primitive object={model} />
      {/* each station spills its own colour onto the floor around it */}
      <pointLight position={[0, 1.1, 0.2]} color={color} intensity={1.6 * power} distance={2.8} decay={2} />
    </>
  );
}

useGLTF.preload("/models/deck.glb");
useGLTF.preload("/models/pedestal.glb");

// The big screen behind the deck: one glowing bar per agent, its height and colour the
// agent's state, sweeping left to right like a status board. Pure WebGL, so it always draws.
function CommandScreen({ agents, quiet }: { agents: AgentSummary[]; quiet: boolean }) {
  const ref = useRef<THREE.Group>(null);
  useFrame(({ clock }) => {
    if (!ref.current) return;
    ref.current.children.forEach((c, i) => {
      const m = (c as THREE.Mesh).material as THREE.MeshBasicMaterial;
      m.opacity = (quiet ? 0.25 : 0.55) + Math.max(0, Math.sin(clock.elapsedTime * 2 - i * 0.45)) * 0.45;
    });
  });
  const w = 8.5, n = Math.max(1, agents.length), gap = 0.12, bw = (w - gap * (n + 1)) / n;
  return (
    <group position={[0, 3.3, -10.5]} rotation-x={0.12}>
      <mesh>
        <planeGeometry args={[w, 2.9]} />
        <meshBasicMaterial color="#29d9f5" transparent opacity={quiet ? 0.03 : 0.07} side={THREE.DoubleSide} />
        <Edges color="#29d9f5" />
      </mesh>
      <group ref={ref} position={[-w / 2 + gap, -1.25, 0.02]}>
        {agents.map((a, i) => {
          const h = a.state === "ok" ? 2.2 : a.state === "late" ? 1.4 : a.state === "failed" ? 0.8 : 0.4;
          return (
            <mesh key={a.id} position={[gap * i + bw * (i + 0.5), h / 2, 0]}>
              <planeGeometry args={[bw, h]} />
              <meshBasicMaterial color={STATE_COLOR[a.state] ?? "#4a6a8a"} transparent opacity={0.6} toneMapped={false} />
            </mesh>
          );
        })}
      </group>
    </group>
  );
}

function RelayBeam() {
  const beam = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    if (beam.current) (beam.current.material as THREE.MeshBasicMaterial).opacity = 0.35 + Math.sin(clock.elapsedTime * 3) * 0.1;
  });
  return (
    <group position={RELAY}>
      <mesh ref={beam} position={[0, 6, 0]}>
        <cylinderGeometry args={[0.12, 0.35, 12, 24, 1, true]} />
        <meshBasicMaterial color="#29d9f5" transparent opacity={0.4} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      <mesh rotation-x={-Math.PI / 2} position={[0, 0.03, 0]}>
        <ringGeometry args={[0.5, 0.7, 48]} />
        <meshBasicMaterial color="#29d9f5" />
      </mesh>
      <Html center position={[0, 0.5, 1.1]} pointerEvents="none" zIndexRange={[5, 0]}>
        <div className="font-display text-[9px] tracking-[.35em] text-cyan/80 whitespace-nowrap">RELAY · TELEGRAM</div>
      </Html>
    </group>
  );
}

// ---------------------------------------------------------------- one station

interface StationProps {
  agent: AgentSummary;
  quiet: boolean;
  selected: boolean;
  talking: number;
  reduce: boolean;
  onSelect: (id: string) => void;
}

function Station({ agent, quiet, selected, talking, reduce, onSelect }: StationProps) {
  const look = LOOK[agent.id];
  const [x, z] = look.pos;
  const isChief = agent.id === "chief-assistant";
  const remote = agent.runs_on === "pc";
  const lastMs = agent.last ? Date.now() - new Date(agent.last.started_at).getTime() : Infinity;
  const delivered = lastMs < 2 * 3600_000;
  const state = agent.state;
  const base = new THREE.Color(state === "failed" ? "#ff4d6d" : state === "late" ? "#f7b538" : state === "paused" || state === "idle" ? "#4a6a8a" : look.color);
  const power = quiet ? 0.25 : state === "paused" || state === "idle" ? 0.35 : 1;
  const scale = isChief ? 1.35 : 1;

  const body = useRef<THREE.Group>(null);
  const head = useRef<THREE.Mesh>(null);
  const orbit = useRef<THREE.Group>(null);
  const mat = useRef<THREE.MeshStandardMaterial>(null);
  const [hover, setHover] = useState(false);

  useFrame(({ clock }) => {
    const t = clock.elapsedTime + x * 0.7;
    if (body.current && !reduce) body.current.position.y = 0.15 + Math.sin(t * 1.3) * 0.06;
    if (orbit.current && !reduce && power > 0.3) orbit.current.rotation.y += 0.012 * (state === "failed" ? 3 : 1);
    if (head.current) {
      const s = 1 + talking * 0.18;
      head.current.scale.setScalar(s);
    }
    if (mat.current) {
      let o = 0.42 * power;
      if (state === "failed" && !reduce) o *= Math.random() > 0.08 ? 1 : 0.2; // flicker
      if (state === "late" && !reduce) o *= 0.75 + Math.sin(t * 4) * 0.25;
      mat.current.opacity = o + (hover || selected ? 0.15 : 0);
      mat.current.emissiveIntensity = (0.9 + talking) * power;
    }
  });

  return (
    <group position={[x, isChief ? 0.3 : 0.12, z]} scale={scale}
           onClick={(e) => { e.stopPropagation(); onSelect(agent.id); }}
           onPointerOver={(e) => { e.stopPropagation(); setHover(true); document.body.style.cursor = "pointer"; }}
           onPointerOut={() => { setHover(false); document.body.style.cursor = ""; }}>
      {/* plinth and console, modelled in Blender */}
      {!isChief && (
        <Suspense fallback={null}>
          <Pedestal color={base} power={power} />
        </Suspense>
      )}
      {selected && (
        <mesh rotation-x={-Math.PI / 2} position={[0, 0.05, 0]}>
          <ringGeometry args={[0.9, 0.96, 64]} />
          <meshBasicMaterial color="#ffffff" transparent opacity={0.8} />
        </mesh>
      )}
      {/* hologram body */}
      <group ref={body} position={[0, 0.15, 0]}>
        {HAS_ART.has(agent.id) ? (
          <Suspense fallback={null}>
            <ArtHologram id={agent.id} state={state} power={power} talking={talking} highlight={hover || selected} reduce={reduce} />
          </Suspense>
        ) : (
          <>
            <mesh position={[0, 0.85, 0]}>
              <capsuleGeometry args={[0.28, 0.75, 8, 20]} />
              <meshStandardMaterial ref={mat} color={base} emissive={base} transparent opacity={0.42}
                                    depthWrite={false} blending={THREE.AdditiveBlending} />
            </mesh>
            <mesh ref={head} position={[0, 1.72, 0]}>
              <icosahedronGeometry args={[0.24, 1]} />
              <meshBasicMaterial color={base} wireframe transparent opacity={0.9 * power} />
            </mesh>
            <mesh position={[0, 1.72, 0]}>
              <sphereGeometry args={[0.13, 16, 16]} />
              <meshBasicMaterial color={isChief ? "#fff6dc" : base} transparent opacity={0.9 * power} />
            </mesh>
          </>
        )}
        <group ref={orbit} position={[0, 1.1, 0]}>
          <mesh rotation-x={Math.PI / 2.4}>
            <torusGeometry args={[0.55, 0.012, 8, 64]} />
            <meshBasicMaterial color={base} transparent opacity={0.8 * power} />
          </mesh>
          <PropGlyph kind={look.prop} color={base} power={power} />
        </group>
        {remote && (
          <mesh position={[0, 1, 0]}>
            <cylinderGeometry args={[0.8, 0.8, 2.4, 24, 1, true]} />
            <meshBasicMaterial color="#3ee6a8" transparent opacity={0.05} side={THREE.DoubleSide} depthWrite={false} />
          </mesh>
        )}
      </group>
      {delivered && !quiet && !reduce && <Packet from={[x, 1.6, z]} />}
      <Html center position={[0, 2.55, 0]} distanceFactor={11} pointerEvents="none" zIndexRange={[5, 0]}>
        <div className="text-center whitespace-nowrap select-none" style={{ opacity: quiet ? 0.5 : 1 }}>
          <div className="font-display text-[12px] tracking-[.18em]" style={{ color: `#${base.getHexString()}` }}>{look.name.toUpperCase()}</div>
          <div className="font-mono text-[9px] tracking-widest text-dim">{look.station.toUpperCase()}{remote ? " · PC" : ""}</div>
        </div>
      </Html>
    </group>
  );
}

// The generated character as a light-only billboard. Black in the image adds nothing, so
// only the glowing figure shows, like a real projection. State changes the tint:
// failed = red, late = amber, paused/idle = cold grey and dim. A projector glitch
// (a brief sideways slip and flash) happens every few seconds; speech brightens it.
function ArtHologram({ id, state, power, talking, highlight, reduce }: {
  id: string; state: string; power: number; talking: number; highlight: boolean; reduce: boolean;
}) {
  const tex = useTexture(`/art/${id}.webp`);
  tex.colorSpace = THREE.SRGBColorSpace;
  const mesh = useRef<THREE.Mesh>(null);
  const mat = useRef<THREE.MeshBasicMaterial>(null);
  const glitchUntil = useRef(0);
  const tint = useMemo(() => new THREE.Color(
    state === "failed" ? "#ff5a78" : state === "late" ? "#ffc861" : state === "paused" || state === "idle" ? "#8aa0b8" : "#ffffff"), [state]);
  const isChief = id === "chief-assistant";
  const h = isChief ? 2.15 : 1.95;

  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    if (!mesh.current || !mat.current) return;
    if (!reduce && Math.random() < 0.004) glitchUntil.current = t + 0.12;
    const glitch = t < glitchUntil.current;
    mesh.current.position.x = glitch ? (Math.random() - 0.5) * 0.08 : 0;
    let o = (0.95 + talking * 0.35 + (highlight ? 0.15 : 0)) * power;
    if (!reduce && state === "failed") o *= Math.random() > 0.1 ? 1 : 0.25;
    if (!reduce && state === "late") o *= 0.8 + Math.sin(t * 4) * 0.2;
    if (glitch) o *= 1.4;
    mat.current.opacity = Math.min(o, 1.4);
    mesh.current.scale.setScalar(1 + talking * 0.02);
  });

  return (
    <Billboard position={[0, h / 2 - 0.05, 0]} lockX lockZ>
      <mesh ref={mesh}>
        <planeGeometry args={[h / 1.5, h]} />
        <meshBasicMaterial ref={mat} map={tex} color={tint} transparent depthWrite={false}
                           blending={THREE.AdditiveBlending} toneMapped={false} />
      </mesh>
    </Billboard>
  );
}

// A small glowing glyph orbiting each hologram: its "prop".
function PropGlyph({ kind, color, power }: { kind: string; color: THREE.Color; power: number }) {
  const geom = useMemo(() => {
    switch (kind) {
      case "clipboard": return <boxGeometry args={[0.16, 0.22, 0.02]} />;
      case "visor": return <torusGeometry args={[0.08, 0.02, 6, 16]} />;
      case "tablet": return <boxGeometry args={[0.2, 0.14, 0.02]} />;
      case "stylus": return <cylinderGeometry args={[0.015, 0.015, 0.26, 6]} />;
      case "dish": return <coneGeometry args={[0.1, 0.08, 16, 1, true]} />;
      case "reticle": return <ringGeometry args={[0.06, 0.1, 4]} />;
      case "headset": return <torusGeometry args={[0.09, 0.015, 6, 16, Math.PI]} />;
      case "hardhat": return <sphereGeometry args={[0.1, 12, 8, 0, Math.PI * 2, 0, Math.PI / 2]} />;
      case "scanner": return <octahedronGeometry args={[0.1]} />;
      case "screens": return <boxGeometry args={[0.22, 0.12, 0.02]} />;
      default: return <tetrahedronGeometry args={[0.1]} />;
    }
  }, [kind]);
  return (
    <mesh position={[0.55, 0, 0]}>
      {geom}
      <meshBasicMaterial color={color} transparent opacity={0.95 * power} side={THREE.DoubleSide} />
    </mesh>
  );
}

// A packet of light travelling from a station to the Relay beam, on a loop.
function Packet({ from }: { from: [number, number, number] }) {
  const ref = useRef<THREE.Mesh>(null);
  const start = useMemo(() => new THREE.Vector3(...from), [from]);
  const end = useMemo(() => new THREE.Vector3(RELAY[0], 3.5, RELAY[2]), []);
  const offset = useMemo(() => Math.random() * 4, []);
  useFrame(({ clock }) => {
    if (!ref.current) return;
    const p = ((clock.elapsedTime + offset) % 4) / 4;
    ref.current.position.lerpVectors(start, end, p);
    ref.current.position.y += Math.sin(p * Math.PI) * 1.6;
    (ref.current.material as THREE.MeshBasicMaterial).opacity = Math.sin(p * Math.PI);
  });
  return (
    <mesh ref={ref}>
      <sphereGeometry args={[0.06, 10, 10]} />
      <meshBasicMaterial color="#ffffff" transparent />
    </mesh>
  );
}
