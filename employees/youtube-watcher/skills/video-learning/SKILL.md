---
name: video-learning
description: "Use when turning a video (YouTube or any transcript) into durable, applicable knowledge rather than a summary. Triggers on 'what can we learn from this video', 'watch this and tell me what's useful', 'mine this talk', 'is this technique worth adopting', or any request to extract techniques, claims, or ideas from video content. Also use when appending to a knowledge base from video sources, or when judging whether a demonstrated technique is worth adopting, testing, or discarding. For a plain transcript fetch or a simple summary, use youtube-content instead."
metadata:
  version: 1.0.0
  author: YouTube Watcher (Hermes fleet)
---

# Video Learning

Turn video into knowledge that changes a decision. A summary restates; a
learning tells someone what to do differently and how confident to be.

## Order of operations

1. **Transcribe first.** No transcript, no analysis. Titles, thumbnails, and
   descriptions are marketing.
   ```bash
   uv run python "<project>\youtube-watcher\transcribe_video.py" "<URL>" --timestamps
   ```
   The transcript is cached under `knowledge/transcripts/`; check the cache
   before refetching. Exit code 2 means no transcript exists - report the video
   as NOT WATCHED and stop. Do not reconstruct content from metadata.
2. **Read the knowledge base** (`knowledge-base.md`) before judging novelty.
   Something already recorded there is not a finding.
3. **Chunk long transcripts.** Over ~50K characters, split at ~40K with ~2K
   overlap, extract from each chunk, then merge and deduplicate.
4. **Extract, score, and write entries** as below.

## What counts as a learning

A learning must survive all four tests. If it fails one, it is a note, not a
learning:

- **Procedure**: can it be restated as steps someone could follow?
- **Delta**: does it change what we would build, test, or stop doing?
- **Source**: is there a timestamp where the claim is actually made or shown?
- **Durability**: will it still be true after the next model or algorithm
  update? If not, say what it depends on.

Discard on sight: motivation, career advice, tool lists with no method, "the
future of X" framing, and anything whose entire payload is that a product
exists.

## Evidence labels

Attach exactly one to every extracted item:

- `DEMONSTRATED` - the video shows it working end to end, on screen.
- `CLAIMED` - asserted by the speaker without a working demonstration.
- `VERIFIED` - confirmed against a primary source outside the video. Name the
  source.

Rules:

- A `CLAIMED` item never reaches "adopt" on a single source.
- Benchmark numbers, pricing, and metrics stated verbally without being shown
  are `CLAIMED`, no matter how confident the delivery.
- Record creator incentives next to the claim when visible: sponsorship,
  affiliate links, their own product, a course. Incentive does not disqualify a
  claim; hiding it does.
- Quote exactly or paraphrase honestly. Never tidy up a quote into something
  stronger than what was said.

## Scoring

Score 1-5 on: applicability, specificity, evidence strength, novelty against
the knowledge base, time to first useful result, durability. Show the average.

- `ADOPT` - average >= 4.0, evidence >= 4.0, specificity >= 4.0.
- `TEST` - average >= 3.5. State the test and its success criterion.
- `WATCH` - interesting, not actionable yet. Say what would make it actionable.
- `DISCARD` - one line on why, so it is not re-mined later.

## Output per video

```
<title> - <channel> - <publish date> - <url>
Verdict: ADOPT | TEST | WATCH | DISCARD | NOT WATCHED (<reason>)

Learnings:
1. <name> [DEMONSTRATED] (12:40-18:05)
   - what: <the technique in two lines, as steps>
   - why it matters: <the delta against how we do it now>
   - caveats: <what it depends on, what the video did not show>
   - score: applicability 5 / specificity 4 / evidence 5 / novelty 3 /
     speed 4 / durability 3 = 4.0 -> ADOPT

Not worth keeping: <one line each, so the next run skips them>
```

## Knowledge base entries

Produce paste-ready entries in the knowledge base's own format:

```
### <short technique or finding name>
- date: YYYY-MM-DD  lane: agents|tooling|saas|techniques|seo|local_seo
- evidence: DEMONSTRATED | CLAIMED | VERIFIED
- source: <channel> - <video title> - <url> - <mm:ss>
- what: one or two lines, concrete enough to act on
- applies to: where this lands in our stack, or why it is parked
```

Never rewrite an existing entry. A correction is a new dated entry that names
the entry it corrects and what changed it.

## Failure modes to avoid

- Summarizing the video instead of extracting from it. If the output reads like
  a recap, it failed.
- Treating a well-produced video as better evidence than a scrappy one.
  Production quality and correctness are unrelated.
- Laundering a claim into a fact by dropping the hedge the speaker used.
- Counting the same idea from three creators as three sources when all three
  are reacting to one announcement. Trace it to the origin.
- Keeping a finding that is true but that nobody here would ever act on.
