# Consensus Skill

Run structured Claude–Codex debates about difficult proofs, documents, design
decisions, paper reviews, and arguments. The runner preserves both participants'
work in resumable sessions, requires explicit convergence, and produces a durable
Markdown transcript.

Defaults are fixed: **`claude-opus-5-5` at `xhigh`** and **`gpt-6.1-sol` at
`xhigh`**. No dynamic model-documentation fetch occurs by default. Per-run
`CONSENSUS_*` overrides remain supported; the selected IDs and efforts are
frozen for the exchange, including synthesis and recovery, and recorded with
provenance. Machine and app defaults are neither inherited nor changed.

The runner implements **protocol 3**. The offline release gate exercises fake
CLIs and adversarial regressions; it does not certify live provider availability
or provider-side session persistence.

## Requirements

- macOS or another POSIX system with Python 3.8 or later;
- authenticated `claude` and `codex` commands on `PATH`;
- Git for installation and updates.
- Network access for authorized provider calls, not default model discovery.

Participants may use ordinary tools, but the debate prompt forbids recursive
consensus runs, model delegation, and delegating the participant's debate role
to a sub-agent in research, recovery, compression, and synthesis. Context and
peer reports are evidence, not permission to expand the task.

## Install one authoritative checkout

Choose one checkout as the sole source of truth. Codex, Claude, and the command
on `PATH` must all use symlinks into it; do not maintain deployed copies.

```bash
CONSENSUS_REPO=/absolute/path/to/consensus-skill
mkdir -p "$(dirname "$CONSENSUS_REPO")"
git clone https://github.com/fitelson/consensus-skill.git "$CONSENSUS_REPO"
cd "$CONSENSUS_REPO"
chmod +x scripts/consensus scripts/test_consensus.py
python3 scripts/test_consensus.py
```

Before creating the links, move any existing regular files or directories at
the three target paths to a timestamped archive **outside**
`~/.codex/skills`, `~/.claude/skills`, and `~/.local/bin`. Backups inside those
live discovery roots can still be mistaken for installed skills or commands.
Leave a symlink that already resolves to the authoritative checkout in place;
inspect and replace a stale or broken symlink. The `ln -s` commands below
intentionally refuse to overwrite an existing target. Do not use `ln -sfn` to
replace a real directory on macOS.

```bash
mkdir -p ~/.codex/skills ~/.claude/skills ~/.local/bin
ln -s "$CONSENSUS_REPO" ~/.codex/skills/consensus
ln -s "$CONSENSUS_REPO" ~/.claude/skills/consensus
ln -s "$CONSENSUS_REPO/scripts/consensus" ~/.local/bin/consensus
```

Ensure `~/.local/bin` is on `PATH` in the current shell, and add the equivalent
setting to the appropriate shell startup file if necessary:

```bash
export PATH="$HOME/.local/bin:$PATH"
command -v consensus
```

Verify that every entry point resolves into the authoritative checkout:

```bash
test -L ~/.codex/skills/consensus
test -L ~/.claude/skills/consensus
test -L ~/.local/bin/consensus
python3 -c 'import os,sys; print(*(os.path.realpath(p) for p in sys.argv[1:]), sep="\n")' \
  ~/.codex/skills/consensus \
  ~/.claude/skills/consensus \
  ~/.local/bin/consensus
```

To update later, pull the authoritative checkout. All three symlinks immediately
use the updated files:

```bash
cd "$CONSENSUS_REPO"
git pull --ff-only
python3 scripts/test_consensus.py
```

## Use

For a question stored in a text file:

```bash
consensus --quiet \
  --save consensus_result.md \
  --progress consensus_result.progress.md \
  < question.txt
```

You may instead pass a non-sensitive question as positional arguments. A
positional question is visible in the runner's command line; questions read
from stdin are not.

For a document audit, convert PDFs to text first and repeat `--context` as
needed:

```bash
pdftotext -layout paper.pdf paper.txt  # provided by Poppler
consensus --quiet \
  --context paper.txt --context appendix.txt \
  --save consensus_review.md \
  --progress consensus_review.progress.md \
  < audit_prompt.txt
```

Run `consensus --help` for the complete CLI reference. Important options are:

- `--resolve-models`: print model IDs, efforts, and provenance as JSON without
  starting a debate or consuming model usage; defaults need no documentation fetch;
- `--quiet`: hide Claude's thinking text while retaining returned reports and journals;
- `--save FILE`: write the atomic Markdown transcript to `FILE`;
- `--progress FILE`: write the fsynced human-readable event log to `FILE`;
- `--max-rounds N`: maximum full Claude–Codex rounds; default 6;
- `--context FILE`: include a text file in every turn; repeatable;
- `--think TOKENS`: aggregate Claude thinking ceiling across research tranches
  and the initial synthesis; default 42000;
- `--claude-tranche-think TOKENS`: per-call thinking cap; default 7000;
- `--checkpoint-deadline SECS`: semantic checkpoint interval in ACTIVE work;
  default 900; distinct from stream activity;
- `--stream-max-bytes N`: raw transport/disk budget per call, including images;
  default 64000000. Media does not consume the semantic report-capture quota;
- `--event-max-bytes N`: per-JSON-event parsing budget; default 8000000;
- `--claude-report-deadline SECS`: compatibility name for the shared stream
  activity interval; default 300, with five consecutive silent intervals;
- `--claude-recovery-timeout SECS`: bounded same-session report-recovery
  deadline; default 300; Claude recovery disables thinking;
- `--claude-turn-timeout SECS`: compatibility name for the common non-resettable
  live research/synthesis call cap; default 1800 for both participants;
- `--codex-timeout SECS`: Codex live-call deadline; default 1800, positive only,
  capped by the common live-call limit; zero is invalid;
- `--overwrite`: explicitly allow existing output reuse; never permits
  artifact collisions or unsafe symlink redirection;
- `--first {claude,codex}`: opening participant; default Claude;
- `--no-synthesize`: return the final agreed turn without a fresh synthesis;
- `--no-save`: do not write the Markdown transcript; the progress log, private
  journals for both participants, and protocol manifest are still written.

## Durability and convergence

Both participants stream into separate runner-owned private fsynced journals.
Each keeps one stable research session across normal bounded tranches and
report recovery. Synthesis uses its own stable session.

Three independent monitors apply to both participants:

- Semantic progress: emit a six-field checkpoint at least every 900 seconds
  of ACTIVE work; two consecutive missed semantic deadlines stop the work.
  ACTIVE time carries across tranches and includes report-only recovery;
  exclude only peer waits/idle time. Recovery has the same six-field requirement.
  Ordinary stream traffic is not a semantic checkpoint.
- Stream liveness: check activity every 300 seconds; five consecutive silent
  intervals cut off the live call. New activity resets only the silence count.
- Live-call cap: stop each research or synthesis call after 1800 seconds by
  default, even if it keeps streaming. Resume the same session for bounded
  300-second report recovery, with no new research or tools.

The default live-call cap is a separate cutoff: with defaults, five silent
intervals (1500 seconds) or two missed semantic deadlines (1800 seconds of
ACTIVE work) can stop a call first. The cap does not replace either rule or
reset semantic state. Normal
unfinished work resumes the same research session at the next tranche.

Every completed participant report must begin with exactly one six-field
`CHECKPOINT` and end with literal `VERDICT: AGREE` or `VERDICT: DISAGREE`, with
no lowercase spelling or surrounding spaces. `new_results` must be
`NONE`, `NONE: reason`, or `NEW: concrete facts`. The checkpoint and verdict
must describe the same completed report; earlier commentary cannot override it.
Two adjacent completed `DISAGREE`/`NONE` reports from either participant end
with a stalled no-consensus outcome; substantive progress resets the count and
both stalled reports remain saved. Transport failure is not semantic disagreement.

Consensus requires two consecutive valid completed `AGREE` reports. Agreement
that a question remains unresolved is agreement on the answer, not a proof.
After consensus, Claude synthesizes the agreed answer with an internal
checkpoint and `VERDICT: AGREE`; those controls are validated, then stripped
for display. Invalid or dissenting synthesis receives bounded same-session
recovery, then falls back to a substantive agreed answer if necessary. An empty
verdict-only report is not a deliverable.

All process waits remain bounded after output EOF. Interruption cleanup must
survive repeated cancellation, reap the owned active model process group, and
drain the termination tail within its five-second budget and stream guard. See
[operations](references/OPERATIONS.md) for the checkpoint schema and boundaries.

The runner forwards its generated participant prompts and context to both model
CLIs through stdin rather than command-line arguments. This avoids disclosing
those generated prompts in process listings and avoids operating-system
argument-size limits. As noted above, a question supplied positionally to the
runner itself remains visible in the runner's command line.

The debate protocol instructs each participant and the synthesizer to return at
most 40,000 tokens and to put longer supporting material in a referenced project
file. A separate conservative **40,000-UTF-8-byte delivery guard** requests
same-session compression when a report is too large. Bytes are not an exact
token count. The guard never blindly truncates decisive arguments, proof steps,
counterexamples, qualifications, or verdicts; unsuccessful compression leaves
the report undelivered rather than falsely treating it as a valid answer.

## Artifacts and privacy

For `--save debate.md --progress debate.progress.md`, the runner maintains:

- `debate.md`: atomic Markdown transcript and final synthesis;
- `debate.progress.md`: fsynced human-readable event log;
- `debate.progress.md.claude-stream.jsonl`: raw Claude stream journal, created
  with mode `0600`;
- `debate.progress.md.codex-stream.jsonl`: raw Codex stream journal, created
  with mode `0600`;
- `debate.progress.md.protocol.json`: runner identity, protocol version, and
  enforced deadlines.

Before writing, the runner reserves disjoint paths for all artifacts and rejects
canonical-path or same-inode aliases. Existing outputs are refused unless
`--overwrite` is explicit; even then, collisions and unsafe symlink redirection
remain forbidden. Defaults use unique per-run names. Atomic replacement uses
unique exclusive temporary files, fsyncs content and directories, and preserves
or narrows private permissions. New sensitive artifacts are private by default.

All new main artifacts, including the transcript, use mode `0600`. Journals
fsync timestamped stdout/stderr read chunks, not one wrapper per CLI event;
event boundaries can cross journal records. Provider calls retain only the
latest report/control records (2 MB maximum), not image/tool payloads or partial
events. Raw streams go to disk with a 64 MB per-call default; individual JSON
events have an 8 MB default. Both transport limits can be explicitly adjusted
without changing report/checkpoint limits. The queue remains bounded to 64
chunks of at most 64 KiB, with a five-second final tail-drain budget. These
limits do not cap journal size accumulated across resumed calls.

Valid semantic checkpoint contents are persisted in the progress log. Per-miss
semantic deadline entries require separate callbacks; checkpoint journaling
alone does not persist the miss counter. Both research monitors and synthesis
register these callbacks, including same-session recovery.

The Markdown transcript is the normal shareable artifact after review; private
permissions are not automatically widened for sharing. The progress log and
raw journals can contain session information, prompts, supplied context, tool
output, or partial model events; keep them private unless reviewed. The protocol
manifest includes local runner metadata and should also be reviewed before
publication.

## Data transmission, cost, and tool permissions

A run sends the question, supplied context, and accumulated debate transcript to
both the configured Claude and Codex providers. Do not provide material that
their services are not authorized to receive. Calls may consume paid model usage
under the authenticated CLI accounts.

The debate permits both participants to use ordinary tools—including local
files, web research, code execution, proof assistants, and computer algebra—
subject to the permissions and approval settings of the installed CLIs. Those
tools may modify files or external state when their existing permissions allow
it. Run sensitive audits in an appropriately restricted environment and review
the transcript and auxiliary artifacts before sharing them.

## Optional model overrides

- `CONSENSUS_CLAUDE_MODEL`
- `CONSENSUS_CLAUDE_EFFORT`
- `CONSENSUS_CODEX_MODEL`
- `CONSENSUS_CODEX_REASONING_EFFORT`

When unset, Claude uses `claude-opus-5-5`/`xhigh` and Codex uses
`gpt-6.1-sol`/`xhigh`. Both are fixed pins; no model documentation is fetched by
default. Set overrides only for an intentional per-run alternative. No machine
or app settings are modified. Account/gateway availability remains enforced by
the CLIs; a rejection is not permission to downgrade silently.

The retained OpenAI metadata compatibility helper validates a deliberately
narrow frontmatter mapping, not general YAML: one literal `latestModelInfo:`
mapping with unique direct fields and a valid scalar `model`. Nested/body/fenced
examples cannot supply the model. The legacy Claude table helper is separate;
neither helper runs for fixed-default selection. An explicit local snapshot
must retain its provenance, not masquerade as live discovery or replace the
fixed default policy implicitly.

## Development and release verification

Read `PROJECT.md`, `SKILL.md`, and `references/OPERATIONS.md` before changing
the runner. Add an offline regression test for every behavioral or recovery
fix. Then run the complete release gate:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" .
python3 -m py_compile scripts/consensus scripts/test_consensus.py \
  scripts/test_hardening.py scripts/test_lifecycle.py
python3 scripts/test_consensus.py
# Run each helper separately unless the main suite invokes it:
python3 scripts/test_hardening.py
python3 scripts/test_lifecycle.py
git diff --check
```

The acceptance suite uses temporary fake Claude and Codex executables. It makes
no model calls, consumes no model credits, and requires no network access.
The main suite invokes both helpers: 88 hardening tests and 15 lifecycle tests,
in addition to 27 orchestrated cases and the interruption/parser checks.
Coverage includes multi-page image streams, individual images larger than the
old capture limit, terminal failures after images, and report/control filtering.
Rerun the gate after changes; an earlier pass is not evidence for a modified runner.

Generated progress logs, raw journals, protocol manifests, default transcripts,
and named audit transcripts are ignored by Git. Explicitly named output files
may not be ignored; inspect `git status` and review every artifact before
committing or publishing it.

## License

MIT; see `LICENSE`.
