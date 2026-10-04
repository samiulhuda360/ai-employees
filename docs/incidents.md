# Incidents and fixes

What went wrong while running the fleet, how it showed up, and what changed. Each fix is in the code. The file and line comments give the context.

## The backup model invented numbers

**What happened.** The main model's quota ran out mid-week. Hermes switched to a backup model, as configured, and every job still "succeeded". The reports looked normal, but some numbers in them had no source.

**Why it mattered.** A failure that looks like success is the worst kind. Nothing in Telegram said anything had changed.

**Fix.**

- Ingest reads the provider's fallback line and stores `used_fallback` on the run ([hq_ingest.py](../hq/server/hq_ingest.py)).
- The relay puts "[backup model ... wrote this; double-check facts]" at the top of the message ([relay_report.py](../employees/claudia/scripts/relay_report.py)).
- HQ shows a "backup model" badge on the agent.
- The guardian reports fallback runs and reads the logs to say when the main quota resets ([fleet_guard.py](../employees/claudia/scripts/fleet_guard.py)).
- Blog and social drafts now always go through a fact-check job on a stronger model.

## The scouts kept recommending the same thing

**What happened.** Growth Scout recommended four variants of the same idea on four days running. Cash Builds recommended the same tool two days in a row. Their inputs change slowly and they re-read their own notes, so nothing pushed them elsewhere.

**Fix.** [scout_memory.py](../hq/server/scout_memory.py) adds two things to each scout's input. The first is every primary pick of the last 30 days, read from `hq.db`, not from the model's memory. The second is the day's *lane*, a topic area that rotates by weekday. A repeat must now be argued for explicitly.

## Ingest deleted ideas that belonged to other runs

**What happened.** Ingest upserts each report (`INSERT ... ON CONFLICT(path) DO UPDATE`) and then attached ideas to `cursor.lastrowid`. After an upsert that *updates*, `lastrowid` holds the id of some earlier insert. Re-parsed ideas were linked to the wrong run, and the cleanup step deleted that run's own ideas.

**Fix.** Always look the run's id up by its path after the upsert. A comment in [hq_ingest.py](../hq/server/hq_ingest.py) explains why.

## The model's thinking became ideas

**What happened.** Some runs began with narration ("Let me check the search results...", "Excellent. Now I have..."). It became the run's headline and occasionally a parsed idea.

**Fix.** `response_of()` in ingest cuts everything before the report's first heading or verdict line, but only when that text looks like narration. The relays do the same with each report's marker (`BLOG IDEAS`, `PROSPECTS`, ...).

## Tomorrow's date in today's report

**What happened.** Models sometimes put tomorrow's date in a report heading, which made the Telegram message look like a scheduling bug.

**Fix.** The relay rewrites the heading's date from the report file's own timestamp ([relay_report.py](../employees/claudia/scripts/relay_report.py)).

## YouTube blocks datacenter IPs

**What happened.** YouTube Watcher could not fetch channel pages or transcripts from the VPS.

**Fix.** It runs on the Windows PC and pushes its reports and knowledge base to the server over SSH ([sync_to_server.py](../employees/youtube-watcher/sync_to_server.py)). Running on a PC brought its own problems, solved by three pieces:

- [retry_pc_jobs.py](../ops/pc/retry_pc_jobs.py) retries an evening job once if the PC was off at the time.
- [pc_requests.py](../ops/pc/pc_requests.py) lets HQ ask the PC for a recheck without the server ever connecting to the PC.
- The guardian warns when nothing has arrived from the PC for 30 hours.

## The wrong YouTube channel

**What happened.** A channel page mentions many channel ids (sidebar, featured, related). Matching the first `channelId` silently returned a neighbouring channel.

**Fix.** Resolve the id from the page's RSS alternate link, then its canonical link ([youtube_signal_collector.py](../employees/youtube-watcher/youtube_signal_collector.py)).

## The PC sync could resend everything

**Risk.** The sync keeps a state file of what it already sent. If a Windows editor or PowerShell ever saved that file with a byte-order mark, parsing would fail, the state would be treated as empty, and every file would be sent again.

**Fix.** Read the state file as `utf-8-sig` ([sync_to_server.py](../employees/youtube-watcher/sync_to_server.py)).

## Rechecks that never finished

**What happened.** "Recheck" in HQ marks an issue as waiting for the agent's next report. If the PC was off, the item waited forever.

**Fix.** After three hours without a new report, the wait ends with a note saying the recheck did not complete ([hq_api.py](../hq/server/hq_api.py), `resolve_rechecks`).

## Lip-sync was an estimate

**What happened.** The offline voice (Kokoro) gives no word timings. The avatar's mouth was driven by an estimate: each word got a share of the audio in proportion to its length.

**Fix.** Neural voices (edge-tts) report where each word starts. Those timings now drive the lip-sync exactly, and the estimate is kept only for the offline fallback ([hq_api.py](../hq/server/hq_api.py), [ClaudiaAvatar.tsx](../hq/web/src/components/ClaudiaAvatar.tsx)).

## Found while preparing this repository

The tests written for the public copy found a bug in Claudia's action allowlist. If the model suggested a page that was not on the list, `re.fullmatch` returned `None`. The next check then did `None |= bool` and crashed `/api/ask` with a 500 instead of dropping the action. The fix wraps the check in `bool()`, and [test_demo_api.py](../tests/test_demo_api.py) now sends four kinds of disallowed action through the endpoint.
