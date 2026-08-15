# Consensus Skill

This package runs structured Claude–Codex debates with resumable Claude
sessions, a runner-owned fsynced stream journal, five-minute activity checks,
bounded recovery, atomic transcripts, and compressed reports.

It is deliberately model-neutral: it uses the recipient machine's Claude and
Codex defaults unless the optional `CONSENSUS_*` environment variables are set.

## Install

Choose one authoritative checkout:

```bash
CONSENSUS_REPO=/absolute/path/to/consensus-skill
git clone https://github.com/fitelson/consensus-skill.git "$CONSENSUS_REPO"
```

Codex and Claude must edit and run that checkout only. Point the installed skill
and command paths at it with symlinks; do not maintain deployed copies:

If any target below already exists as a regular file or directory, move it to
a timestamped archive outside `~/.codex/skills`, `~/.claude/skills`, and
`~/.local/bin` first. Backups inside those live discovery roots can still be
mistaken for installed skills or commands. Do not use `ln -sfn` to replace a
real directory on macOS.

```bash
ln -s "$CONSENSUS_REPO" ~/.codex/skills/consensus
ln -s "$CONSENSUS_REPO" ~/.claude/skills/consensus
ln -s "$CONSENSUS_REPO/scripts/consensus" ~/.local/bin/consensus
```

Make the runner and test executable, then validate:

```bash
chmod +x scripts/consensus scripts/test_consensus.py
python3 scripts/test_consensus.py
```

The tests use temporary fake Claude/Codex executables and make no network or
model calls.

## Use

```bash
consensus --quiet --save result.md "Question to debate"
```

For documents, convert PDFs to text first and pass each file with `--context`.
The Markdown transcript is the normal shareable artifact. The sibling raw
`.claude-stream.jsonl` file can contain prompts, context, tool output, and
partial events; it is created with mode `0600` and must remain private.

## Optional model overrides

- `CONSENSUS_CLAUDE_MODEL`
- `CONSENSUS_CLAUDE_EFFORT`
- `CONSENSUS_CODEX_MODEL`
- `CONSENSUS_CODEX_REASONING_EFFORT`

If unset, the installed CLI defaults are used.

## Development

Validate the skill metadata and run the offline acceptance suite before
committing changes:

```bash
python3 /path/to/skill-creator/scripts/quick_validate.py .
python3 scripts/test_consensus.py
```

Generated progress logs and raw Claude stream journals are ignored by Git. The
raw journals can contain private prompts, supplied context, and tool output;
review artifacts before publishing them.

## License

MIT; see `LICENSE`.
