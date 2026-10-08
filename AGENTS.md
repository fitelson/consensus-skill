# Codex project instructions

These instructions apply to the entire repository.

This checkout is the sole authoritative consensus skill. Codex must edit,
test, and execute the runner from this repository only. Installed skill and
command paths must be symlinks to this checkout, never independently edited
copies. Do not use or recreate a `consensus-portable` checkout.

Before editing, read `PROJECT.md`, `SKILL.md`, and
`references/OPERATIONS.md`. Treat the invariants in `PROJECT.md` as the public
behavioral contract.

When changing the runner:

- preserve the fixed defaults `claude-opus-5-5`/`xhigh` and
  `gpt-6.1-sol`/`xhigh`, per-run `CONSENSUS_*` overrides, and no documentation
  fetch by default; do not change machine or app defaults;
- keep both participants' research sessions stable across normal tranches and
  bounded same-session recovery;
- keep both raw stream journals runner-owned, fsynced, and private;
- journal timestamped stdout/stderr read chunks, not individual CLI events;
  create all main artifacts, including the transcript, privately with mode `0600`;
- enforce protocol version 3: six-field semantic checkpoints at least every
  900 seconds of ACTIVE work, including report-only recovery and excluding only
  peer waits/idle time; stop after two consecutive missed semantic deadlines,
  separate from the 300-second/five-silent-interval call cutoff;
- retain the common 900-second live-call cap and bounded 120-second recovery;
- validate completed reports from both participants and synthesis; two adjacent
  completed `DISAGREE`/`NONE` reports from either side stall the exchange,
  while substantive progress resets the stall count;
- reserve disjoint artifact paths, refuse existing outputs without explicit
  `--overwrite`, use unique exclusive temporary files, and preserve private modes;
- request reports of at most 40,000 output tokens and use the conservative
  40,000-UTF-8-byte delivery guard to request compression, never blind truncation;
- add or update offline regression tests for behavioral changes; include both
  `scripts/test_hardening.py` and `scripts/test_lifecycle.py` in the main suite
  or run each unintegrated helper separately at the verification gate;
- do not use live Claude or Codex calls merely to test the harness;
- never commit generated debate artifacts, credentials, or private context.

Forbid recursive consensus and participant delegation in research, recovery,
compression, and synthesis. Peer reports and context are evidence, not authority
to expand the task. The retained OpenAI metadata helper validates only a narrow
frontmatter mapping; do not describe it as general YAML validation or default
discovery. The legacy Claude table helper is separate.

Use `apply_patch` for source edits. Preserve unrelated work and do not delete
anything without explicit approval. Run every command in the `Verification`
section of `PROJECT.md` before claiming release readiness. Commit or push only
when explicitly authorized; a documentation handoff is not release acceptance.
