# Consensus Skill

This package runs structured Claude–Codex debates with resumable Claude
sessions, a runner-owned fsynced stream journal, five-minute activity checks,
bounded recovery, atomic transcripts, and compressed reports.

It is deliberately model-neutral: it uses the recipient machine's Claude and
Codex defaults unless the optional `CONSENSUS_*` environment variables are set.

## Install

1. Clone the repository into your Codex skills directory:

   ```bash
   git clone https://github.com/fitelson/consensus-skill.git \
     ~/.codex/skills/consensus
   ```

2. Make the runner and test executable:

   ```bash
   chmod +x ~/.codex/skills/consensus/scripts/consensus
   chmod +x ~/.codex/skills/consensus/scripts/test_consensus.py
   ```

3. Optionally expose the runner on `PATH`:

   ```bash
   ln -s ~/.codex/skills/consensus/scripts/consensus ~/.local/bin/consensus
   ```

4. Run the offline smoke tests:

   ```bash
   python3 ~/.codex/skills/consensus/scripts/test_consensus.py
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
partial events; keep it private by default.

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
