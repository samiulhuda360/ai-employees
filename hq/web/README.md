# Hermes HQ web app

React 19, TypeScript, Vite, Tailwind CSS and react-three-fiber.

```bash
npm ci
npm run dev      # http://localhost:5173, proxies /api to the HQ API on 127.0.0.1:8787
npm run lint
npm run build    # dist/, served by nginx in production
```

- `src/pages/`: Deck (3D command deck), Agents, Ideas, Build and Write boards, Prospects, Customers, Me, Log.
- `src/scene/DeckScene.tsx`: the deck. It loads `public/models/deck.glb`, built by `hq/blender/build_deck.py`.
- `src/components/Claudia.tsx` and `ClaudiaAvatar.tsx`: voice in and out, plus the TalkingHead avatar. TalkingHead and three.js are served as plain ES modules from `public/` (see the import map in `index.html`).
- `src/lib/api.ts`: the API client. Every write sends the `x-hq` header that the API requires.

To rebuild the deck model:

```bash
blender -b --factory-startup --python ../blender/build_deck.py -- public/models
```
