# Setup

## The demo (any machine)

See [Run the demo](../README.md#run-the-demo). In short:

```bash
pip install -r hq/server/requirements.txt
python hq/demo/seed.py
cd hq/server && HQ_DEMO=1 HQ_HOME=../demo/home uvicorn hq_api:app --port 8787
cd hq/web && npm ci && npm run dev
```

Open http://localhost:5173 and use the access key `demo`. Run `seed.py` again at any time for fresh data dated today.

### Claudia's avatar

The 3D avatar file is not included because the one used in production cannot be redistributed. To add your own:

1. Export a half-body or full-body avatar as GLB with ARKit and Oculus viseme blend shapes. Avaturn and similar tools can do this.
2. Save it as `hq/web/public/avatars/claudia.glb`.

Without it, the avatar panel explains what is missing and Claudia still speaks.

### Voices

Without extra packages, Claudia uses the browser's speech. For the voices used in production, install `hq/server/requirements-voice.txt`:

- **edge-tts:** neural voices with word timings.
- **Kokoro:** offline. Put `kokoro-v1.0.onnx` and `voices-v1.0.bin` in `$HQ_HOME/hq/models/`.

## The real fleet

You need [Hermes Agent](https://github.com/NousResearch/hermes-agent) installed on a Linux server, a model provider, and a Telegram bot for Claudia's profile.

1. **Profiles.** Create one Hermes profile per agent, named as in `fleet/jobs.yaml` (Claudia's is `chief-assistant`). Copy the agent's `SOUL.md` into the profile, and its collectors and configs into `~/agents/<agent>/`.
2. **Jobs.** Create each job in [fleet/jobs.yaml](../fleet/jobs.yaml) in that agent's profile, using the same schedule, pre-run script, prompt and delivery. Jobs marked `model: none` are no-agent jobs: the script's output is the message. HQ later controls them with `hermes -p <agent> cron run|pause|resume|edit`.
3. **Telegram.** Claudia's profile holds the bot, and every report reaches Telegram through it. Some are forwarded by Claudia's no-model relay jobs (agents that deliver `local`). Others are delivered to a named chat (`chat:` in jobs.yaml).
4. **HQ.**
   - Copy `hq/server` to `~/hq/server`, make a virtualenv and install the requirements.
   - Run `hq_api:app` with uvicorn on 127.0.0.1:8787, as a user systemd service named `hq-api`.
   - Run `hq_ingest.py` from cron every 10 minutes.
   - Build `hq/web` and serve `dist/` with nginx, proxying `/api/` to the API. Use HTTPS, because the session cookie is `Secure`.
   - Put `HQ_PASSWORD_HASH=pbkdf2$<iterations>$<salt hex>$<hash hex>` in `~/hq/.env`.
5. **PC agents (optional).** Install Hermes on the PC for YouTube Watcher and Code Health. Schedule `sync_to_server.py` and the two scripts in [ops/pc](../ops/pc) with Task Scheduler. `run_hidden.vbs` runs them without a console window.

`<project>` in instructions and prompts means the folder where you keep the PC agents.

## Configuration to change

| File | What |
|---|---|
| `employees/content-scout/content-seeds.yaml` | Your site, sitemap, market and competitors |
| `employees/content-scout/social-config.yaml` | Platforms and posting rules |
| `employees/prospect-finder/prospect-config.yaml` | Market, niches, cities, spend limit (DataForSEO) |
| `employees/opportunity-scout/cash-config.yaml` | Build limits, price points, channels you own |
| `employees/growth-scout/product-dossier.md` | What your product already does |
| `employees/code-health/projects.yaml` | Repositories to audit |
| `employees/youtube-watcher/watchlist.yaml` | Channels and searches to learn from |
