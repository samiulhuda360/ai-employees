# Architecture

## Where things run

| Machine | What runs there | Why |
|---|---|---|
| VPS (small) | Seven agents, Claudia's relays and guardian, HQ (API, ingest, nginx) | Always on; reachable from Telegram and the browser |
| Windows PC | YouTube Watcher, Code Health | YouTube blocks datacenter IPs, and the repositories live on the PC |

The PC pushes its reports to the server over SSH ([sync_to_server.py](../employees/youtube-watcher/sync_to_server.py)). The server never connects to the PC. When HQ wants a PC agent to run again, it drops a request file on the server. A Windows task ([pc_requests.py](../ops/pc/pc_requests.py)) collects it within five minutes, runs the job and syncs the result. A second task ([retry_pc_jobs.py](../ops/pc/retry_pc_jobs.py)) retries an evening job once, an hour after its slot, if it failed or did not run.

The sync is built to be safe to run as often as needed:

- It sends only new or changed files, judged by modification time and size, as one compressed archive over SSH. Its record of what was sent is updated only after the server accepts the archive.
- `config.yaml` and `.env` are never copied, because they can hold tokens.
- It reads its state file as `utf-8-sig`, so a byte-order mark added by a Windows editor cannot empty the state and cause a full resend.

## An agent

An agent is a Hermes Agent *profile*: a folder with `SOUL.md` (the agent's instructions), memories, and `cron/jobs.json`. A job has:

- a schedule (cron, local time)
- a **pre-run script** (the collector). Its standard output is placed in the prompt under "Script Output".
- a prompt
- a delivery target: Telegram through Claudia, a direct chat, or `local` (stored only)
- optionally `no_agent: true`. The script's output is then the whole message, so no model runs.

Hermes writes each run to `cron/output/<job id>/<timestamp>.md`.

### Collect first, then reason

Each collector ([example](../employees/content-scout/content_signal_collector.py)) does the work that must be exact:

- fetching feeds, search results and suggestions, and live Google Maps results
- reading the HQ database for what was already recommended
- reading repository lock files and calling OSV.dev for advisories
- fetching the product's customer metrics

It then prints a compact block. The model's job is narrower: check what needs a live look, choose, explain and write. Blog Planner, for example, reads the live top 10 for a query and runs `dr_check.py` on those sites before it calls a topic winnable. Instructions require a source for every claim and forbid numbers that are not in a source.

### Novelty memory for the scouts

Growth Scout and Cash Builds work from inputs that change slowly, so their collectors append a block from [scout_memory.py](../hq/server/scout_memory.py):

- **Today's lane:** one of seven topic areas per scout, rotating by weekday. The day's primary recommendation must come from it.
- **Already recommended:** every pick of the last 30 days, read from `hq.db`, so the list does not depend on the model's own notes.
- **Rules:** no repeat or close variant as the primary pick. New, dated evidence from the last 7 days earns one "Update on ..." line, and the primary pick is still new. Ideas the founder rejected or parked stay closed unless the founder reopens them.

### What YouTube Watcher teaches the fleet

YouTube Watcher runs on the PC every evening:

- Its collector builds a ranked shortlist of new videos from the channels and searches in `watchlist.yaml`. A channel listed by its @handle is resolved to its ID from the channel page's RSS alternate link, then its canonical link, never from the first channel ID that appears on the page. Resolved IDs are cached. When a feed fails, the collector falls back to the channel's videos page.
- The model must fetch a transcript ([transcribe_video.py](../employees/youtube-watcher/transcribe_video.py)) before judging a video. A video it has not transcribed is marked NOT WATCHED.
- Each technique it keeps is graded VERIFIED, DEMONSTRATED or CLAIMED, with timestamps, and qualifying ones are appended to its knowledge base.

The sync mirrors the knowledge base to the server. [learnings.py](../hq/server/learnings.py) routes each entry to the agents named on its `for:` line, or by its lane. The collectors of Blog Planner, Growth Scout and Cash Builds add the last 21 days of learnings for that agent to its input. VERIFIED and DEMONSTRATED items may shape a recommendation; a CLAIMED item is a lead to check, never a fact to state.

### Fact-check jobs and relays

Blog Planner and Social Planner draft on a cheaper model. A second job, 30 minutes later and pinned to a stronger model, reads the draft ([blog_draft_for_review.py](../employees/content-scout/blog_draft_for_review.py)), re-opens the sources and removes anything it cannot confirm.

Only Claudia's profile holds the Telegram bot. Agents that deliver `local` are forwarded by her relay jobs, which run with no model ([relay_report.py](../employees/claudia/scripts/relay_report.py)):

- The relay sends the checked version when it is newer than the draft. Otherwise it sends the draft with an "unchecked" line on top.
- The message starts at the report's own marker (`BLOG IDEAS`, `PROSPECTS`, ...), so anything the model wrote before the report is left out.
- The date in the heading comes from the report file's timestamp.
- A run written by the backup model starts with "[backup model ... wrote this; double-check facts before acting]".
- If the latest batch is more than 20 hours old, the message says the agent did not run today. A failed run is reported as a failure, not relayed.
- [tg_pretty.py](../employees/claudia/scripts/tg_pretty.py) shortens the message for Telegram using only the report's own words. If a report does not match the expected shape, the original text is sent.

Code Health's relay runs hourly and sends each new report from the PC once.

## Data flow into HQ

```
cron/output/*.md ─┐
pc-reports/*.md ──┼─> hq_ingest.py ──> hq.db ──> hq_api.py ──> web
prospects.csv ────┤      (every 10 min, idempotent: key = path + content hash)
pulse-history ────┘
```

[hq_ingest.py](../hq/server/hq_ingest.py), in order:

1. **Upserts agents and jobs** from every `jobs.json`, and from the PC's mirrored status file.
2. **Parses each report:** job name, run time and FAILED marker. It detects a backup-model run from the provider's fallback line and stores the backup model's name. When the text before the report's first heading or verdict line reads like narration ("Let me check...", "Excellent."), it is cut.
3. **Stores the run.** After each upsert, the run's id is read back by its path, so re-parsed ideas always attach to their own run.
4. **Extracts ideas** with the agent's parser in [hq_parsers.py](../hq/server/hq_parsers.py). An idea's key is `agent | type | title`, so the same idea raised again updates one row. Decisions are kept: re-parsing a run replaces only its undecided ideas.
5. **Loads side files:** the prospect ledger, the weekly customer pulse, the ideas backlog, and YouTube Watcher's knowledge base.

`hq_ingest.py --reparse` re-reads every stored report with the current parsers.

Idea types are `blog`, `social`, `cash_build`, `saas`, `feature`, `quick_win`, `prospect`, `customer_win`, `customer_risk`, `customer_lead`, `code_fix`, `learning`, `proposal`, `agent_idea` and `upgrade`.

## Priority

[hq_priority.py](../hq/server/hq_priority.py) scores each open idea from 0 to 100:

- **Base points by type and signal.** For example, a blog idea gets +25 when the top 10 was checked and is beatable. A Growth Scout verdict counts BUILD 85, EXPERIMENT 62, WATCH 25. A code fix is scored by severity.
- **Decay by age, per type.** A social post loses 4 points a day; a code fix loses 0.3.
- **Repeats add points.** An idea raised on several days merges into one row and scores up to +15.
- **Snapshot types keep only the latest run.** Code fixes and customer signals: an item missing from the newest run drops off the list.

The result is P1 (70+, do first), P2 (50–69) or P3 (counted, not shown). Each score lists its reasons, and the UI shows them as tags.

## Rechecks

The **Recheck** button on an agent's Priority tab asks whether an issue is still there ([hq_api.py](../hq/server/hq_api.py)):

- **Server agents** run their main job again at once. One run settles every issue waiting on that agent.
- **PC agents** get a request file on the server. [pc_requests.py](../ops/pc/pc_requests.py) collects it within five minutes, runs the job and syncs the report back.
- **Code issues** are checked directly on the PC: the lock files for the named package version, or `git status` for uncommitted files. The answer comes back as a small result file, with no full audit. If the issue names nothing checkable, the full audit runs instead.

When the agent reports again, an issue that is still in the new report (same or near-same wording) stays open with a note. One that is gone is marked done as "Fixed" and shown in a Fixed panel for a day. If no new report arrives within three hours, for example because the PC is off, the wait ends with a note that the recheck did not complete.

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
- **Acting:** the API checks the action against an allowlist (navigate, run a *server* job, set an idea's status, log to the journal) and drops anything else. The UI asks for confirmation before running a job or changing an idea.
- **Speaking:** neural voices (edge-tts) report word boundaries, which drive TalkingHead's lip-sync exactly. Kokoro (offline, on the server) is the fallback; it gives no word timings, so the avatar spreads each sentence's audio over its words in proportion to their length. The browser's own speech is the last resort.

## Backup-model runs

When the main model is unavailable, Hermes answers with a backup model and adds a provider-fallback line to the report. Those runs are flagged everywhere they appear:

- Ingest stores `used_fallback` and the backup model's name on the run.
- HQ shows a "backup model" chip on the agent's card and in its run history, a banner on the deck, and an alert in the top bar.
- Claudia's live context in HQ carries a warning, so her answers mention it.
- The relays put a "backup model" line at the top of the Telegram message.
- The guardian lists backup-model runs from the last 24 hours. When the logs show the main model's quota is exhausted, it says roughly when the quota resets.
- Blog and social drafts are reviewed by the fact-check job on a stronger model, and the relay sends the checked version when it exists.

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

Quiet hours run from 23:00 to 07:00. Claudia's instructions keep Telegram messages out of that window, every job that delivers to Telegram is scheduled between 07:00 and 23:00, and HQ's top bar shows "messages held" during quiet hours.

## Demo mode

`HQ_HOME` points the API, the ingest and the guardian at another home folder. `HQ_DEMO=1` replaces the three things a demo machine does not have, using [demo.py](../hq/server/demo.py):

| Production | Demo |
|---|---|
| Telegram login code | shown on the login screen |
| `hermes` CLI | edits the demo's `jobs.json` the same way; "run now" re-issues the job's last report |
| Claudia's model | a rule-based stand-in returning the same JSON, checked by the same allowlist |

[seed.py](../hq/demo/seed.py) writes the files a real fleet writes, with made-up content, and runs the real ingest. The demo therefore exercises the same parsers, scoring and API as production.
