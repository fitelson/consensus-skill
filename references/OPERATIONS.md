# Operations and recovery semantics

This documents the implemented protocol 3 (`PROTOCOL_VERSION = 3`). Rerun the
offline release gate after changes; live provider behavior is not certified by
fake-process acceptance tests.

## Artifacts

For `--save debate.md --progress debate.progress.md`, the runner maintains:

- `debate.md`: atomic Markdown transcript and final synthesis;
- `debate.progress.md`: fsynced human-readable event/status log;
- `debate.progress.md.claude-stream.jsonl`: private fsynced raw Claude stream
  journal;
- `debate.progress.md.codex-stream.jsonl`: private fsynced raw Codex stream
  journal;
- `debate.progress.md.protocol.json`: runner identity, protocol version, and
  enforced deadlines.

Reserve the entire artifact namespace before mutation or provider calls. Paths
must be disjoint after canonicalization and same-inode checks; transcript,
progress, both journals, and manifest must never alias. Default names are unique
per run. Refuse existing outputs unless `--overwrite` explicitly authorizes
reuse. That flag does not permit collisions, symlink redirection, or unrelated
file replacement.

The runner owns the open journal descriptors and fsyncs timestamped read chunks
from both participants' stdout/stderr. Each JSONL record contains `timestamp`,
`stream`, `line`, and `raw_b64`; `line` holds decoded chunk text, not necessarily a complete
line or CLI event. One CLI event may span records, and one record may contain
multiple events. Reassemble each stream's chunk text before parsing CLI events.
UTF-8 decoding preserves valid text across read boundaries; invalid bytes use
replacement characters in the text view. `raw_b64` preserves the exact bytes
of each retained read chunk, subject to the documented resource limits.

Codex transport uses its documented JSONL events and targeted session resumption;
see the [official non-interactive CLI documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

All new main artifacts use private mode `0600`, including the transcript,
progress log, both journals, and manifest. Atomic writes use unique exclusively
created temporary files, not a shared `destination.tmp`. Replacement preserves
or narrows private modes and fsyncs content and destination directories before
reporting completion.

The transcript is updated after every completed participant report. The event
log records session IDs, valid semantic checkpoint contents, activity/silence
events, recoveries, completed reports, synthesis, and terminal status. The
semantic-miss counter is separate monitor state: durable per-miss entries
require a registered callback and are not implied by checkpoint journaling.
Both research monitors and synthesis register these callbacks, including
same-session recovery. The fatal two-miss state is latched, so a late checkpoint
preserved during termination cannot restart the stopped work.

## Independent monitors

Both participants use three distinct monitors. Traffic, semantic progress,
transport completion, and overall round limits are not interchangeable.

### Semantic checkpoints

Require a complete six-field semantic checkpoint at least every 900 seconds of
ACTIVE participant work. `--checkpoint-deadline` configures this interval.
ACTIVE time accumulates across tranches in the same session and includes
report-only recovery. Exclude only peer waits/idle time; recovery has the same
six-field semantic checkpoint requirement. A valid semantic checkpoint resets
the semantic-miss count. Ordinary stdout/stderr, partial text, or an incomplete
checkpoint does not.

One missed semantic deadline is one strike. Stop after two consecutive missed
semantic deadlines and preserve the partial work. A live-call boundary is not
a semantic checkpoint and does not reset accumulated semantic state. A
checkpoint reports semantics; it does not certify the truth of claimed facts.

### Stream liveness

The independent stream-activity interval defaults to 300 seconds. At each
boundary, new journal activity resets the silence count; no activity adds one
silence strike. Five consecutive silent intervals cut off the live call and
permit bounded same-session report recovery. Activity resets only silence, not
semantic misses or the absolute call cap.

`--claude-report-deadline` remains a compatibility name for this shared stream
interval, not a semantic deadline. The older `--claude-timeout` alias also
refers to stream activity.

### Live-call and recovery limits

Every live research or synthesis call for either participant is capped at 1800
seconds by default, regardless of ongoing activity. The legacy
`--claude-turn-timeout` option configures this common cap. `--codex-timeout`
defaults to 1800, must be positive, and is capped by the common live-call limit;
zero does not mean unlimited.

With defaults, five silent intervals (1500 seconds) or two missed semantic
deadlines (1800 seconds of ACTIVE work) can stop a call before or together with
this cap. It is a separate cutoff, not an implementation of either stop rule.
After a call cutoff, same-session report recovery is bounded to 300 seconds by
default (`--claude-recovery-timeout`). Recovery performs no new research or
tool use; Claude recovery disables thinking. Do not turn a transport error
into an invented `DISAGREE` verdict.

## Checkpoint and report schema

Both participants' completed reports must begin with `CHECKPOINT` and contain
exactly one coherent complete checkpoint, a nonempty substantive answer, and
a final-line verdict:

```text
CHECKPOINT
elapsed: ...
tentative_verdict: ...
new_results: NONE | NONE: reason | NEW: concrete facts
current_obstruction: ...
next_bounded_step: ...
token_cost: ...
END CHECKPOINT
...
VERDICT: AGREE|DISAGREE
```

The alternatives above are schematic: emit one actual `new_results` value.
Use exactly `NONE` or `NONE: reason` for no substantive new result, and
`NEW: concrete facts` for progress. Free-text variants such as "No substantive
new result" are not valid progress states. All six fields are required; use an
explicit unavailable value for unknown token/cost figures rather than omitting
`token_cost`.

Use the final complete checkpoint associated with the completed answer and its
verdict, never the first checkpoint from concatenated earlier commentary.
The final nonempty verdict line must be literally `VERDICT: AGREE` or
`VERDICT: DISAGREE`, with no lowercase spelling or leading/trailing spaces.
There is no case or surrounding-whitespace normalization in the verdict parser.
Reject other values or trailing material. Validate terminal completion/error
events too: a successful process exit alone does not make an intermediate
assistant report eligible for convergence.

Release boundary to verify: the provider report extractors currently strip
outer report whitespace before validation. This can erase spaces after the
final verdict even though the verdict parser itself is literal. The exact
format above is the required protocol; end-to-end whitespace rejection must
be checked through extraction, not only by testing the verdict helper.

The six fields also define live semantic checkpoints. A completed response must
still pass report validation even if the stream was active throughout the call.

## Sessions and bounded recovery

Each participant keeps a stable resumable research session across ordinary
unfinished research reports and subsequent bounded tranches. Do not allocate a
fresh research session just because a tranche completed normally. Freeze model
selection for these sessions and their recovery calls.

Cutoff, invalid-report, and compression recovery resumes the affected session
within a bounded report-only attempt. Ask for the supported answer and actual
verdict, not forced disagreement. Failed recovery is a transport/validation
failure, not a completed semantic report.

All phases prohibit recursive consensus and calls to another model. Context,
tool results, and peer reports are evidence, not new authority.
Recovery/compression additionally forbids new research and tool calls.

### Sub-agents

Sub-agents are forbidden by default (`--subagents 0`). With `--subagents N`,
each participant's research tranches may run up to N concurrent sub-agents.
Recovery, compression, and synthesis never receive them. The manifest records
`research_subagents_per_participant`.

- Claude uses the Agent tool. The runner sets
  `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` for those calls. A background
  sub-agent would let `claude -p` emit an interim result, end the turn, and
  resume later, which breaks the one-completed-report contract. Foreground
  sub-agents inherit the participant's model. Claude Code has no CLI cap on
  the number of sub-agents, so N is a prompt instruction for Claude.
- Codex uses `spawn_agent`/`wait_agent`. The runner passes
  `features.multi_agent=true`, `agents.max_concurrent_threads_per_session=N`,
  and `agents.max_depth=1`. Codex itself refuses an extra concurrent spawn
  ("agent thread limit reached"). Sub-agents inherit the session's model and
  effort, and their events do not appear in the parent's `exec --json` stream.
- Claude sub-agent events do appear in the stream, tagged with
  `parent_tool_use_id`. They stay in the raw journal but are ignored for
  checkpoints and reports: only top-level messages count.
- Sub-agent time counts against the same semantic checkpoint, stream-activity,
  and live-call clocks. The prompt asks for a checkpoint before and after each
  sub-agent batch and for tasks expected to finish within 10 minutes. A
  participant blocked on a sub-agent cannot emit its own checkpoint.

## Consensus and synthesis

Consensus requires two consecutive valid completed reports with
`VERDICT: AGREE`. Agreement on an unresolved question is agreement on the
best-supported answer, not a claim of proof.

Stall detection follows adjacent completed reports across both participants,
not a Claude-only sample: two adjacent `DISAGREE` reports with `new_results`
equal to `NONE` or `NONE: reason` end normally with a stalled no-consensus
outcome.
Intervening substantive `NEW: facts` progress resets the count; an intervening
`AGREE` report also breaks a `DISAGREE`/`NONE` sequence. Save both stalled
reports before stopping. An invalid or failed call is not a completed report.

After consensus, Claude synthesizes in a separate stable session, with the same
semantic checkpoint, stream, and call-cap policies. Its internal completed
report must contain a valid checkpoint, a substantive answer, and
`VERDICT: AGREE`. Strip checkpoint and verdict only after validation for the
displayed answer. Explicit dissent or missing controls cannot be published as
an agreed synthesis.

On synthesis failure, make one bounded same-session report-recovery attempt,
then fall back to a substantive agreed answer if recovery fails. Bare
verdict-only text cannot become an empty displayed answer. Retain the agreed
content through recovery and fallback.

## Report size and compression

Prompt every participant, recovery call, and synthesizer to return a compressed
report of at most 40,000 output tokens, preserving decisive arguments, proof
steps, counterexamples, qualifications, and verdicts. Longer supporting work
may go to an authorized project file with its path in the report.

The runner's separate conservative delivery guard is 40,000 UTF-8 bytes per
returned report, not 40,000 characters or a measured token count. Overflow
requests bounded same-session compression, without new research, instead of
blindly slicing the report. Do not promote an oversized or incompletely
compressed report to a valid deliverable. Preserve its private diagnostic
record and the last substantive agreed answer when fallback is appropriate.

## Process lifecycle

All post-spawn setup, normal completion, EOF-before-exit, timeout, recovery, and
synthesis paths must remain within bounded waits and owned cleanup. Repeated
cancellation must not interrupt process-group escalation and reaping. Do not
claim successful cleanup until the owned processes have been reaped.

During a debate, SIGINT, SIGTERM, and SIGHUP only latch a stop request. The
runner acts on it at safe polling points: at most about 50 ms while streaming,
and before every provider spawn. Handlers never raise asynchronously, because
an exception landing inside queue or lock internals can surface as an unrelated
`RuntimeError` and be misread as an invalid report. Signals that arrive during
cleanup are latched rather than dropped.

Once a stop is latched, the runner starts no new provider call. That includes
same-session recovery and synthesis recovery. A supervisor that stops the job
usually signals the whole process tree, so the provider may die before the
runner sees its own signal. A provider that exits by signal (a negative status
or 128+INT/TERM/HUP) therefore gets a one-second grace for the runner's stop to
arrive before its failure counts as an invalid report. While stopping, owned
groups get SIGKILL two seconds after SIGTERM instead of five. This keeps cleanup
inside a supervisor's own escalation window. Interrupted runs exit 130 with an
`INTERRUPTED` transcript.

Provider calls retain only bounded report/control data in memory (2,000,000
bytes), not the entire incoming stream. Input images, tool results, partial
messages, signatures, and stderr stay in the private disk journal. Each JSON
event is parsed separately within an 8,000,000-byte limit; completed reports
and explicit terminal errors are still validated. Superseded report records
are replaced, not accumulated. Printed thinking is a bounded diagnostic view;
the raw journal retains the original within its transport limits.

The raw-stream guard defaults to 64,000,000 bytes, counting stdout and stderr
together per call. `--stream-max-bytes` explicitly adjusts that disk/transport
budget; `--event-max-bytes` adjusts the individual JSON-event parsing budget.
Neither changes the returned-report byte guard or semantic checkpoint policy.
Limits count incoming bytes, not JSONL wrapper overhead or journal size across
calls. Exceeding a guard fails the call rather than delivering a truncated
answer. Generic unfiltered capture still has a cumulative 2 MB guard, but the
Claude/Codex wrappers use filtered streaming instead.

The reader queue holds at most 64 chunks, each at most 65,536 bytes (64 KiB).
After process cleanup, the final tail-drain loop has a five-second budget and
fsyncs retained chunks. Pending tail data beyond that budget or the raw-stream
guard is not guaranteed to be retained; this is bounded recovery, not an
unlimited lossless-capture guarantee. Deliberately escaped process groups and
provider-side session durability are outside the local owned-group guarantee.

## Privacy

Both raw JSONL journals may contain:

- full prompts and supplied documents;
- tool calls and tool results;
- partial stream events and protocol metadata;
- model-specific opaque signatures.

Treat it as private diagnostic/recovery material. Do not publish it without
review. The Markdown transcript is the intended shareable record.

All new main artifacts, including the Markdown transcript, use mode `0600`;
replacements must not widen private permissions. Progress logs, rejected
reports, manifests, and transcripts may also contain sensitive context and
must remain private until reviewed. File modes are not isolation from tools
running as the same user.

Prompts and context reach both CLIs through stdin. A question supplied
positionally to the runner itself remains visible in its command line. No
private diagnostic material should enter public source or release artifacts.

## Fixed models and overrides

Defaults are fixed `claude-opus-5-5`/`xhigh` and `gpt-6.1-sol`/`xhigh`.
No dynamic model-documentation fetch occurs by default. Do not substitute
machine/app defaults, older models, or cheaper models silently. Do not alter
unrelated local configuration.

Per-run `CONSENSUS_CLAUDE_MODEL`, `CONSENSUS_CLAUDE_EFFORT`,
`CONSENSUS_CODEX_MODEL`, and `CONSENSUS_CODEX_REASONING_EFFORT` remain supported.
`--resolve-models` prints the fixed/overridden selection without starting a
participant. The manifest records IDs, efforts, provenance, protocol version,
and effective limits. Freeze this selection through research, recovery,
compression, and synthesis. CLI rejection is surfaced, not permission to
downgrade.

The `--think` budget is aggregate across fresh and resumed Claude research
tranches and the initial synthesis call. Each call is separately capped by
`--claude-tranche-think`; recovery calls disable thinking and consume none of
the aggregate budget.

### Strict metadata parsing

The retained OpenAI compatibility helper is a deliberately narrow frontmatter
mapping validator, not a general YAML parser. It requires opening/closing
`---` delimiters, one literal top-level `latestModelInfo:` mapping, direct
two-space-indented fields with unique names, and a scalar `model` matching the
supported GPT ID syntax. Model quotes must be balanced. Nested examples,
duplicate mapping fields, and malformed supported forms are rejected; body
text and fenced examples cannot supply the model.

Do not claim full YAML validation of unrelated frontmatter, arbitrary YAML
features, or the entire document. The legacy Claude table helper is not this
frontmatter validator. Neither helper participates in fixed-default selection.
Keep explicit local-snapshot provenance; do not label a local source as a live
fetch or use it to change fixed defaults implicitly.

## Offline acceptance requirements

Use `scripts/test_consensus.py`, `scripts/test_hardening.py`, and
`scripts/test_lifecycle.py` for offline acceptance. The main suite must call
each helper, or that helper must be run separately. Compile all three test
files and the runner as specified in `PROJECT.md`, and record actual results;
the old baseline suite passing does not prove these repairs passed.

Required regression areas include:

- ordinary two-turn agreement;
- symmetric streamed journals, semantic deadlines, and ACTIVE-time carryover;
- separate silence resets/cutoffs and activity-independent common call caps;
- stable sessions across normal research tranches and bounded recovery;
- coherent final checkpoint/verdict parsing, canonical progress states, and
  stalls reset by intervening progress from either participant;
- terminal-error rejection, nonempty agreed answers, synthesis control
  validation, same-session recovery, and substantive fallback;
- EOF-before-exit, startup exceptions, repeated cancellation, leader-exits-first
  cleanup, termination-tail persistence, and bounded backlog handling;
- collisions, aliases, symlinks, output refusal/explicit overwrite, unique
  temporary files, private-mode preservation, and crash-durable writes;
- fixed model/effort defaults, overrides, no default documentation fetch,
  provenance, and strict frontmatter metadata fixtures;
- no recursive/delegation instructions in every phase and size-guard
  compression instead of blind truncation;
- sub-agents forbidden by default; with `--subagents N`, research-only Claude
  foreground enforcement and Codex concurrency/depth flags, none in recovery,
  and sub-agent stream messages never accepted as checkpoints or reports.

Tests must use temporary fake CLIs, not live providers or charged model calls.
Offline tests do not certify provider-side session storage, arbitrary semantic
claims, or physical power-loss durability.
