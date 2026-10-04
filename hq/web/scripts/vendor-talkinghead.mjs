// Copy TalkingHead and the three.js files it imports into public/, so the browser loads
// them as plain ES modules (TalkingHead imports its lip-sync module by a computed path,
// which a bundler cannot follow). index.html maps "three" and "three/addons/" to these.
//
//   node scripts/vendor-talkinghead.mjs
import { cpSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const nm = join(root, "node_modules");
const pub = join(root, "public");

// TalkingHead modules
cpSync(join(nm, "@met4citizen/talkinghead/modules"), join(pub, "talkinghead"), { recursive: true });

// three core
mkdirSync(join(pub, "vendor/three/build"), { recursive: true });
for (const f of ["three.module.min.js", "three.core.min.js"]) {
  cpSync(join(nm, "three/build", f), join(pub, "vendor/three/build", f));
}

// three addons TalkingHead imports, plus everything they import relatively.
const jsm = join(nm, "three/examples/jsm");
const queue = ["controls/OrbitControls.js", "loaders/GLTFLoader.js", "loaders/DRACOLoader.js",
  "loaders/FBXLoader.js", "environments/RoomEnvironment.js", "libs/stats.module.js"];
const seen = new Set();
while (queue.length) {
  const rel = queue.pop();
  if (seen.has(rel)) continue;
  seen.add(rel);
  const src = join(jsm, rel);
  const dst = join(pub, "vendor/three/examples/jsm", rel);
  mkdirSync(dirname(dst), { recursive: true });
  cpSync(src, dst);
  for (const m of readFileSync(src, "utf8").matchAll(/from\s+['"](\.{1,2}\/[^'"]+)['"]/g)) {
    queue.push(join(dirname(rel), m[1]).replace(/\\/g, "/"));
  }
}
console.log(`talkinghead + three core + ${seen.size} addon files copied`);
if (!existsSync(join(pub, "avatars/claudia.glb"))) console.log("note: public/avatars/claudia.glb is missing");
