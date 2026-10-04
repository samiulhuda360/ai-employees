# Architecture

## Where things run

| Machine | What runs there | Why |
|---|---|---|
| VPS (small) | Seven agents, Claudia's relays and guardian, HQ (API, ingest, nginx) | Always on; reachable from Telegram and the browser |
| Windows PC | YouTube Watcher, Code Health | YouTube blocks datacenter IPs, and the repositories live on the PC |

The PC pushes its reports to the server over SSH ([sync_to_server.py](../employees/youtube-watcher/sync_to_server.py)). It only sends files whose modification time or size changed. The server never connects to the PC. When HQ wants a PC agent to run again, it drops a request file on the server. A Windows task ([pc_requests.py](../ops/pc/pc_requests.py)) collects it within five minutes, runs the job and syncs the result. A second task ([retry_pc_jobs.py](../ops/pc/retry_pc_jobs.py)) retries evening jobs once if the PC was off at the scheduled time.

## An agent

An agent is a Hermes Agent *profile*: a folder with `SOUL.md` (the agent's instructions), memories, and `cron/jobs.json`. A job has:

- a schedule (cron, local time)
- a **pre-run script** (the collector). Its standard output is placed in the prompt under "Script Output".
- a prompt
- a delivery target: Telegram through Claudia, a direct chat, or `local` (stored only)
- optionally `no_agent: true`. The script's output is then the whole message, so no model runs.

Hermes writes each run to `cron/output/<job id>/<timestamp>.md`.

### Collect first, then reason

Each collector ([examples](../employees/content-scout/content_signal_collector.py)) does the work that must be exact:

- fetching feeds and search results
- reading the HQ database for what was already recommended
- checking the live top 10 for a query
- calling OSV.dev for advisories

It then prints a compact block. The model's job is narrower: choose, explain and write, citing only what is in that block. Instructions forbid numbers that are not in a source.

### Fact-check jobs

Blog Planner and Social Planner draft on a cheaper model. A second job, 30 minutes later and pinned to a stronger model, reads the draft ([blog_draft_for_review.py](../employees/content-scout/blog_draft_for_review.py)), re-opens the sources and removes anything it cannot confirm. The relay ([relay_report.py](../employees/claudia/scripts/relay_report.py)) sends the checked version when it is newer than the draft. Otherwise it sends the draft, labelled as unchecked. A backup-model run is labelled too.

## Data flow into HQ

```
cron/output/*.md ─┐
pc-reports/*.md ──┼─> hq_ingest.py ──> hq.db ──> hq_api.py ──> web
prospects.csv ────┤      (every 10 min, idempotent: key = path + content hash)
pulse-history ────┘
```

[hq_ingest.py](../hq/server/hq_ingest.py), in order:

1. **Upserts agents and jobs** from every `jobs.json`, and from the PC's mirrored status file.
2. **Parses each report:** job name, run time and FAILED marker. It detects a backup-model run from the provider's fallback line, and cuts the model's narration before the first heading.
3. **Extracts ideas** with the agent's parser in [hq_parsers.py](../hq/server/hq_parsers.py). An idea's key is `agent | type | title`, so the same idea raised again updates one row. Decisions are kept.
4. **Loads side files:** the prospect ledger, the weekly customer pulse, the ideas backlog, and YouTube Watcher's knowledge base.

Idea types are `blog`, `social`, `cash_build`, `saas`, `feature`, `quick_win`, `prospect`, `customer_win`, `customer_risk`, `customer_lead`, `code_fix`, `learning`, `proposal`, `agent_idea` and `upgrade`.

## Priority

[hq_priority.py](../hq/server/hq_priority.py) scores each open idea from 0 to 100:

- **Base points by type and signal.** For example, a blog idea gets +25 when the top 10 was checked and is beatable. A Growth Scout verdict counts BUILD 85, EXPERIMENT 62, WATCH 25. A code fix is scored by severity.
- **Decay by age, per type.** A social post loses 4 points a day; a code fix loses 0.3.
- **Repeats add points.** An idea raised on several days merges into one row and scores up to +15.
- **Snapshot types keep only the latest run.** Code fixes and customer signals: if the newest run no longer mentions an item, it was fixed.

The result is P1 (70+, do first), P2 (50–69) or P3 (counted, not shown). Each score lists its reasons, and the UI shows them as tags.

## The self-improvement loop

```
founder decides ideas in HQ ──┐
YouTube Watcher learnings ────┼─> tuneup_collector.py ─> Claudia (Sunday 17:30)
past proposals ───────────────┘                                │
                                         "TUNE-UP PROPOSALS" report
                                                               │
                                    ingest: one 'proposal' idea per rule, owned by the target agent
                                                               │
                         approve in HQ ─> rule appended to that agent's SOUL.md (dated backup first)
                         reject/park   ─> rule removed again
```

Only four agents can be tuned (`TUNABLE` in [hq_api.py](../hq/server/hq_api.py)). Every rule carries a tag (`[hq-<id>]`), so it can be traced to the proposal and removed cleanly.

## Claudia's voice

- **Listening:** the browser's speech recognition, push-to-talk or a wake word.
- **Thinking:** `/api/ask` sends Claudia's model a snapshot of the fleet (states, the P1 list, undecided ideas, today's journal). She must answer with one JSON object, `{"say": ..., "action": ...}`.
- **Acting:** the API checks the action against an allowlist (navigate, run a *server* job, set an idea's status, log to the journal) and drops anything else. The UI asks for confirmation before running it.
- **Speaking:** neural voices (edge-tts) report word boundaries, which drive TalkingHead's lip-sync exactly. Kokoro (offline, on the server) is the fallback, and the browser's own speech is the last resort.

## Health

The guardian ([fleet_guard.py](../employees/claudia/scripts/fleet_guard.py)) runs three times a day with no model. It checks:

- failed runs in the last 24 hours
- runs on the backup model (and when the main model's quota resets)
- jobs more than 2 hours past their slot
- an ingest stalled for more than 45 minutes
- the HQ API or nginx being down
- a disk over 85% full
- PC sync older than 30 hours

When everything is fine it prints nothing, so Claudia sends nothing.

Quiet hours (23:00–07:00) hold Claudia's Telegram messages.

## Demo mode

`HQ_HOME` points the API, the ingest and the guardian at another home folder. `HQ_DEMO=1` replaces the three things a demo machine does not have, using [demo.py](../hq/server/demo.py):

| Production | Demo |
|---|---|
| Telegram login code | shown on the login screen |
| `hermes` CLI | edits the demo's `jobs.json` the same way; "run now" re-issues the job's last report |
| Claudia's model | a rule-based stand-in returning the same JSON, checked by the same allowlist |

[seed.py](../hq/demo/seed.py) writes the files a real fleet writes, with made-up content, and runs the real ingest. The demo therefore exercises the same parsers, scoring and API as production.
