# Project context

## Purpose

This repository maintains a model-neutral Codex skill and command-line runner
for structured Claude–Codex debates. Its central concern is durability: a long
Claude turn should remain recoverable even when the live process becomes
silent or crosses a monitoring boundary.

## Authoritative checkout

This repository is the single source of truth for the consensus skill. Codex
and Claude must edit, test, and run this checkout only. The installed Codex
skill path and any command on `PATH` must be symlinks into this checkout, not
independently maintained copies. The former `consensus-portable` path is
retired and must not be recreated.

## Architecture

- `SKILL.md` defines when and how Codex should use the skill.
- `scripts/consensus` implements debate orchestration and recovery.
- `scripts/test_consensus.py` provides offline acceptance tests with fake CLIs.
- `references/OPERATIONS.md` documents artifacts and recovery semantics.
- `agents/openai.yaml` supplies Codex UI metadata.

## Required invariants

Preserve these properties in every release:

1. Use recipient-machine model defaults unless an optional `CONSENSUS_*`
   environment variable explicitly overrides one.
2. Give each Claude research turn and synthesis a stable, resumable session ID.
3. Have the runner own and fsync the raw Claude stream journal.
4. Treat stream activity and the validity of a returned checkpoint as separate
   conditions.
5. Check stream activity every five minutes by default and stop only after five
   consecutive silent intervals.
6. Impose a 15-minute absolute cap on every live Claude research turn and
   synthesis, regardless of stream activity.
7. Recover by resuming the same Claude session with a bounded no-thinking call.
8. Require the six checkpoint fields and a trailing `VERDICT` from completed
   Claude reports.
9. Require two consecutive `VERDICT: AGREE` reports before synthesis, treating
   agreement that a matter is unresolved as AGREE.
10. Cap returned participant reports at 40,000 tokens; put longer material in a
   referenced project file.
11. Treat raw `.claude-stream.jsonl` journals as private by default.
12. Terminate the complete active model process group on interruption.
13. Deliver prompts through stdin and create raw journals with mode `0600`.
14. Accept only exact final-line verdicts and preserve completed stalled turns.
15. Make transcript and manifest replacement crash-durable with file and
    directory fsyncs.

Do not permit recursive consensus debates or participant model delegation.

## Verification

Run all of the following after changing the skill or runner:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" .
python3 -m py_compile scripts/consensus scripts/test_consensus.py
python3 scripts/test_consensus.py
git diff --check
```

The acceptance suite must remain offline: it uses temporary fake Claude and
Codex executables and must not consume model credits or require network access.

## Repository policy

- Keep `SKILL.md` concise and place operational detail in `references/`.
- Add regression coverage for every runner or recovery bug.
- Never commit progress logs, raw stream journals, credentials, local paths, or
  private debate context.
- Do not delete tracked files or saved artifacts without explicit maintainer
  approval.
- Keep `main` releasable: validate before committing and verify the pushed
  commit on the public remote.

## Current status

The current release includes stable-session recovery, runner-owned fsynced
journaling, the five-silent-interval policy, a non-resettable 15-minute live
turn cap, process-group cleanup on interruption, convergence semantics for
unresolved verdicts, same-session synthesis recovery and fallback, private
stdin prompt transport, strict verdict parsing, crash-durable artifacts, and
offline regression coverage.
