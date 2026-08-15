# Claude Code project instructions

This checkout is the sole authoritative consensus skill. Claude must edit,
test, and execute the runner from this repository only. Installed skill and
command paths must be symlinks to this checkout, never independently edited
copies. Do not use or recreate a `consensus-portable` checkout.

Read `PROJECT.md`, `SKILL.md`, and `references/OPERATIONS.md` before modifying
this repository. The invariants in `PROJECT.md` are the public behavioral
contract.

This project develops the consensus harness itself. Do not invoke that harness
recursively, start a Claude–Codex debate, or delegate to another model while
working on it unless the maintainer explicitly asks for a live acceptance test.
Prefer the bundled offline suite, whose fake CLIs exercise orchestration without
network access or model charges.

For every behavioral change:

- preserve model neutrality, stable session IDs, runner-owned fsynced
  journaling, five-interval silence handling, the absolute live-turn cap, and
  same-session recovery;
- keep stream activity distinct from returned-checkpoint validity;
- add a regression test in `scripts/test_consensus.py`;
- keep raw stream journals and supplied debate context private;
- avoid committing credentials, generated artifacts, or machine-specific paths;
- do not delete files or artifacts without explicit maintainer approval.

Run the complete `Verification` block in `PROJECT.md` before claiming success.
Keep reports concise and preserve decisive evidence, failure modes, and exact
test results.
