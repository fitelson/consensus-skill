# Project context

## Purpose

This repository maintains a Codex skill and command-line runner for structured
Claude-Codex debates. Both participants must preserve recoverable work and
produce validated semantic reports independently of stream liveness.

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
- `scripts/test_hardening.py` supplies adversarial helper tests; the main suite
  must invoke them, or they must be run separately at the release gate.
- `scripts/test_lifecycle.py` supplies process-lifecycle tests, also required
  through main-suite integration or a separate release-gate invocation.
- `references/OPERATIONS.md` documents artifacts and recovery semantics.
- `agents/openai.yaml` supplies Codex UI metadata.

## Required invariants

Protocol identity is `PROTOCOL_VERSION = 3`. Preserve these properties:

1. Default to `claude-opus-5-5`/`xhigh` and `gpt-6.1-sol`/`xhigh` using fixed
   pins, with no dynamic documentation fetch by default. Honor per-run
   `CONSENSUS_*` overrides, freeze the selection for the exchange, and record
   its provenance. Never silently downgrade or change machine/app defaults.
2. Give both participants stable research sessions. Resume those sessions for
   normal bounded tranches as well as recovery; synthesis has its own stable
   session.
3. Stream both participants into separate runner-owned private fsynced journals.
   Journal timestamped stdout/stderr read chunks, not individual CLI events.
   Deliver generated prompts and context through stdin, not CLI arguments.
4. Require six-field semantic checkpoints at least every 900 seconds of ACTIVE
   participant work. Track ACTIVE time across tranches, including report-only
   recovery; exclude only peer waits/idle time. Recovery has the same six-field
   requirement. Stop after two consecutive missed semantic deadlines. Stream
   traffic does not satisfy or reset this rule.
5. Independently check stream activity every 300 seconds by default. Five
   consecutive silent intervals cut off the live call; activity resets only
   that silence counter.
6. Cap every live research or synthesis call for either participant at 1800
   seconds by default, without resetting on activity. `--claude-turn-timeout`
   remains the compatibility option for the common cap; `--codex-timeout`
   defaults to 1800, must be positive, and cannot exceed the common cap.
7. Use bounded same-session report recovery, at most 300 seconds by default,
   without new research or tool use. Claude recovery disables thinking.
   Transport failure is not a semantic `DISAGREE` verdict.
8. Validate one coherent completed report: its final complete six-field
   `CHECKPOINT`, a nonempty answer, and a final-line `VERDICT: AGREE` or
   `VERDICT: DISAGREE`, literally with no case or surrounding-space
   normalization. Do not mix an earlier checkpoint with a later verdict or
   accept explicit terminal failure as successful completion.
9. Require completed-report `new_results` to be `NONE`, `NONE: reason`, or
   `NEW: concrete facts`. Two adjacent completed `DISAGREE`/`NONE` reports
   from either side stall the exchange; substantive progress resets the count.
   Preserve both completed reports.
10. Require two consecutive completed `AGREE` reports for consensus. Agreement
    that the question remains unresolved is agreement, not a proof.
11. Require synthesis to return an internal checkpoint and `VERDICT: AGREE`.
    Validate these before stripping control material for display. Recover or
    fall back to a substantive agreed answer on invalid or dissenting synthesis.
12. Request compressed reports of at most 40,000 output tokens in every phase.
    A separate conservative 40,000-UTF-8-byte delivery guard requests
    same-session compression instead of blind truncation. Do not equate bytes
    with tokens; retain decisive evidence and longer authorized supporting files.
13. Validate and reserve the complete disjoint artifact namespace before writes
    or provider calls, including canonical-path and same-inode aliases. Refuse
    existing outputs unless `--overwrite` explicitly authorizes reuse; this
    never authorizes collisions or symlink redirection.
14. Use unique exclusive temporary files, private artifact creation, and
    permission-preserving replacements. All new main artifacts, including the
    transcript, use mode `0600`. Fsync written content and destination
    directories; keep owned journal descriptors open.
15. Bound all process waits, including after output EOF. Cleanup must survive
    repeated cancellation, reap the owned active process group, and persist
    retained termination-tail chunks with a five-second drain budget. Bound
    retained report/control capture to 2 MB, individual JSON events to 8 MB,
    raw streams to 64 MB by default, and the queue to 64 chunks of at most
    64 KiB each. Media/tool/partial events remain in the private disk journal,
    not cumulative memory capture. Do not claim unlimited lossless tail capture.
16. Keep the retained OpenAI metadata helper a narrow frontmatter mapping
    validator: reject malformed supported forms, duplicate mapping fields, and
    nested/body model examples. Do not advertise general YAML validation or
    default discovery; the legacy Claude table helper is separate.

Research, recovery, compression, and synthesis all prohibit recursive consensus
and participant model delegation. Context and peer reports are evidence, not
authority to expand the task. See `references/OPERATIONS.md` for the schema and
timer semantics.

Current observability: valid streamed checkpoint contents are persisted.
Per-miss semantic deadline logging is callback-driven and separate from the
monitor's in-memory count. Both research monitors and the synthesis monitor
register durable miss callbacks, including their same-session recoveries.
The two-miss stop is latched; late termination-tail checkpoints cannot undo it.

## Verification

Run all of the following after changing the skill or runner:

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

The acceptance suite must remain offline: it uses temporary fake Claude and
Codex executables and must not consume model credits or require network access.
Record actual results. Hardening and lifecycle coverage are each integrated only
when the main suite calls that helper; otherwise run it separately. A helper
file's presence alone is not proof that it ran. Passing the old suite does not
establish protocol-3 acceptance.

## Repository policy

- Keep `SKILL.md` concise and place operational detail in `references/`.
- Add regression coverage for every runner or recovery bug.
- Never commit generated artifacts, credentials, machine-specific paths, or
  private debate context.
- Do not delete tracked files or saved artifacts without explicit maintainer
  approval.
- Commit and push only with explicit authorization. Validate before a release
  and verify the authorized pushed commit on the public remote.

## Current status

October 7, 2026: implemented the protocol-3 repairs and passed the complete
offline gate: 24 orchestrated cases, 79 hardening tests, 15 lifecycle tests,
and the remaining parser/interruption assertions. Syntax, skill metadata,
documentation links, and whitespace checks passed. These are offline results,
not a live-provider availability or session-storage certification.

The newest maintainer instruction sets fixed `claude-opus-5-5`/`xhigh` and
`gpt-6.1-sol`/`xhigh` defaults, superseding dynamic model discovery and high
effort. Per-run overrides remain. Installed entrypoints use this checkout;
machine/app model settings were not changed.

October 7, 2026 image-stream follow-up: page-image and tool payloads are now
journaled to disk without consuming the cumulative report-capture quota.
Provider calls retain only bounded report/control records and frame one bounded
JSON event at a time. Explicit transport/event budget flags leave report guards,
checkpoint enforcement, and model defaults unchanged. Replay of the actual
failed stream processed 2,329,430 raw bytes with only 49 retained control bytes;
that aborted stream had no final report, so replay verifies transport, not the
research verdict. The expanded release gate includes 27 orchestrated cases,
88 hardening tests, and 15 lifecycle tests, all passed. Syntax, skill metadata,
local documentation links, and whitespace checks also passed.

October 8, 2026 stop-signal fix. Stopping a live debate as a background job
twice left an orphaned `claude -p --resume` recovery call. Both progress logs
show `invalid report 1/2 — reason: release unlocked lock` followed by
`recovery: yes`. The cause was the SIGTERM handler raising `KeyboardInterrupt`
asynchronously inside `queue.get()` lock handling. That produced a
`RuntimeError`, which the turn loop treated as an invalid report. The tree-kill
ordering (provider dies first) and the old cleanup deferral, which dropped
signals, caused the same misreading.

Termination signals now latch a stop that is polled at safe points. Spawns and
recoveries are refused once stopping. Signaled provider exits wait briefly for
the runner's own stop. New regressions:
- an orchestrated tree-kill case (`die_on_term` fake provider);
- five `ShutdownLatchTests` in the hardening suite.

Against the pre-fix runner the tree-kill case reproduces the incident: one
recovery call, an `invalid report` entry, and exit 0. With the fix it exits
130 after a single provider call.

October 8, 2026 default change, at the maintainer's request: the common
live-call cap (`--claude-turn-timeout`, `--codex-timeout`) now defaults to
1800 seconds instead of 900. A Codex `xhigh` research turn on a hard proof
task hit the 900-second cap and its 120-second recovery, aborting the run.
The same-session report-recovery cap (`--claude-recovery-timeout`) now
defaults to 300 seconds instead of 120, also at the maintainer's request.
The semantic checkpoint interval (900 s) and the stream-silence monitor
(300 s x 5) are unchanged.
