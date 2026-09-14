# Consensus Skill

Run structured Claude–Codex debates about difficult proofs, documents, design
decisions, paper reviews, and arguments. The runner preserves long Claude work
in resumable sessions, requires explicit convergence, and produces a durable
Markdown transcript.

At each debate startup the runner resolves the latest documented OpenAI
reasoning flagship and Claude demanding-reasoning flagship, and explicitly
runs both at **high** effort. The selected IDs remain fixed for the entire
exchange, including synthesis, and are recorded with source URLs and a timestamp.
Machine model/effort defaults are not used. Explicit per-run pins remain supported.

## Requirements

- macOS or another POSIX system with Python 3.8 or later;
- authenticated `claude` and `codex` commands on `PATH`;
- Git for installation and updates.
- Network access to the public OpenAI and Anthropic model documentation for
  dynamic selection, unless both models are explicitly pinned.

Participants may use ordinary tools, but the debate prompt forbids recursive
consensus runs, model delegation, and delegating the participant's debate role
to a sub-agent.

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
  starting a debate or consuming model usage;
- `--quiet`: hide streamed Claude thinking while retaining returned reports;
- `--save FILE`: write the atomic Markdown transcript to `FILE`;
- `--progress FILE`: write the fsynced human-readable event log to `FILE`;
- `--max-rounds N`: maximum full Claude–Codex rounds; default 6;
- `--context FILE`: include a text file in every turn; repeatable;
- `--think TOKENS`: aggregate Claude thinking ceiling across fresh research
  turns and the initial synthesis; default 42000;
- `--claude-tranche-think TOKENS`: per-call thinking cap; default 7000;
- `--claude-report-deadline SECS`: activity interval; default 300;
- `--claude-recovery-timeout SECS`: no-thinking recovery deadline; default 120;
- `--claude-turn-timeout SECS`: non-resettable live research/synthesis cap;
  default 900;
- `--codex-timeout SECS`: Codex turn deadline; default 1200, with 0 unlimited;
- `--first {claude,codex}`: opening participant; default Claude;
- `--no-synthesize`: return the final agreed turn without a fresh synthesis;
- `--no-save`: do not write the Markdown transcript; the progress log, private
  stream journal, and protocol manifest are still written.

## Durability and convergence

Each Claude research turn and the final synthesis receives a stable session ID.
The runner:

1. streams Claude events into a runner-owned, fsynced private journal;
2. checks for activity every five minutes by default;
3. terminates after five consecutive silent intervals or an independent
   15-minute absolute deadline, whichever comes first (with the defaults, the
   absolute deadline comes first);
4. resumes the same session for a bounded no-thinking recovery report;
5. validates each Claude research report's six-field checkpoint and exact
   final-line verdict;
6. kills the complete active model process group on interruption.

Consensus requires two consecutive completed turns ending exactly in
`VERDICT: AGREE`. Agreement that a question remains unresolved counts as
agreement on the answer; it does not count as proving the proposition under
discussion. Two completed no-progress Claude reports end normally as
`NO CONSENSUS — STALLED`. After consensus, Claude synthesizes the agreed answer
in a separate stable session, with one same-session recovery attempt before the
runner falls back to the last agreed turn.

The runner forwards its generated participant prompts and context to both model
CLIs through stdin rather than command-line arguments. This avoids disclosing
those generated prompts in process listings and avoids operating-system
argument-size limits. As noted above, a question supplied positionally to the
runner itself remains visible in the runner's command line.

The debate protocol instructs each participant and the synthesizer to return at
most 40,000 tokens and to put longer supporting material in a referenced project
file. The runner does not mechanically truncate or reject an overlong report.

## Artifacts and privacy

For `--save debate.md --progress debate.progress.md`, the runner maintains:

- `debate.md`: atomic Markdown transcript and final synthesis;
- `debate.progress.md`: fsynced human-readable event log;
- `debate.progress.md.claude-stream.jsonl`: raw Claude stream journal, created
  with mode `0600`;
- `debate.progress.md.protocol.json`: runner identity, protocol version, and
  enforced deadlines.

The Markdown transcript is the normal shareable artifact. The progress log and
raw journal can contain session information, prompts, supplied context, tool
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

When unset, models are resolved from current official documentation and both
efforts are explicitly `high`. Set these overrides only for an intentional
alternative, not to reproduce an unrelated local default. No global settings
are modified.

Model discovery reads OpenAI's `latestModelInfo.model` from
[the latest-model guide](https://developers.openai.com/api/docs/guides/latest-model.md)
and the unique most-capable/demanding-reasoning column of Anthropic's
[current model comparison table](https://platform.claude.com/docs/en/about-claude/models/overview.md).
It does not rank every mentioned version number or pick a cheaper model because
its release is newer. If documentation is unreachable, malformed, or ambiguous,
the run stops before either CLI starts. There is no silent stale-cache fallback.
Account/gateway availability is still enforced by the CLIs; a rejection is not
permission to downgrade. Third-party provider IDs may need explicit pins.

For offline tests or an intentionally supplied documentation snapshot,
`CONSENSUS_MODEL_DOCS_DIR` may point to a directory containing `openai.md` and
`claude.md`. This is an explicit source override, not an automatic cache. The
local source paths are recorded in place of URLs. The test suite uses fixtures
with future model IDs to check that releases are not hard-coded.

## Development and release verification

Read `PROJECT.md`, `SKILL.md`, and `references/OPERATIONS.md` before changing
the runner. Add an offline regression test for every behavioral or recovery
fix. Then run the complete release gate:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" .
python3 -m py_compile scripts/consensus scripts/test_consensus.py
python3 scripts/test_consensus.py
git diff --check
```

The acceptance suite uses temporary fake Claude and Codex executables. It makes
no model calls, consumes no model credits, and requires no network access.

Generated progress logs, raw journals, protocol manifests, default transcripts,
and named audit transcripts are ignored by Git. Explicitly named output files
may not be ignored; inspect `git status` and review every artifact before
committing or publishing it.

## License

MIT; see `LICENSE`.
