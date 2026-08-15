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

- preserve model neutrality and stable Claude session recovery;
- keep the raw stream journal runner-owned, fsynced, and private by default;
- add or update an offline regression test for behavioral changes;
- do not use live Claude or Codex calls merely to test the harness;
- do not weaken the five-consecutive-silent-interval rule, absolute live-turn
  cap, or checkpoint schema
  without an explicit maintainer decision;
- never commit generated debate artifacts, credentials, or private context.

Use `apply_patch` for source edits. Preserve unrelated work and do not delete
anything without explicit approval. Run every command in the `Verification`
section of `PROJECT.md` before committing. If release or publication is
requested, inspect the diff, commit on `main`, push, and verify that local and
remote commit IDs agree.
