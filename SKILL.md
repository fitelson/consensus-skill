---
name: consensus
description: Use when an agent should consult both local Claude Code and Codex, or run a structured Claude-vs-Codex debate about a difficult proof, document, design decision, paper review, or argument. Trigger on requests such as "ask Claude", "use consensus", "have Claude and Codex debate", "get a debated second opinion", or "audit this with both models". Uses recipient-machine model defaults unless optional environment overrides are set.
---

# Consensus Debate

Use the bundled `scripts/consensus` runner to conduct a rigorous Claude–Codex exchange until two consecutive participants return `VERDICT: AGREE`, or the round limit is reached.

## Requirements

- `claude` and `codex` must be installed, authenticated, and on `PATH`.
- The runner intentionally uses each recipient machine's model and effort defaults.
- Optional overrides are environment variables, not hard-coded skill policy:
  `CONSENSUS_CLAUDE_MODEL`, `CONSENSUS_CLAUDE_EFFORT`,
  `CONSENSUS_CODEX_MODEL`, and `CONSENSUS_CODEX_REASONING_EFFORT`.

## Mandatory durability protocol

Every Claude research turn has a stable resumable session ID. The runner:

1. requests `stream-json` with partial messages;
2. fsyncs every Claude stream event to a private `.claude-stream.jsonl` journal;
3. checks that journal for new activity every five minutes by default;
4. resets the silence count whenever new activity appears;
5. terminates the live process only after five consecutive silent intervals;
6. resumes the same Claude session for bounded no-thinking recovery;
7. validates the six-field returned `CHECKPOINT` block and trailing verdict;
8. atomically updates the Markdown debate transcript after each completed turn.

The required checkpoint fields are `elapsed`, `tentative_verdict`,
`new_results`, `current_obstruction`, `next_bounded_step`, and
`token_cost`. A missing field or verdict is an invalid returned report.

The final synthesis has its own stable Claude session, uses the same durable
stream journal and silence monitor, and falls back to the last agreed turn if
synthesis fails.

## Confidentiality boundary

The raw JSONL journal can contain the full prompt and supplied context, tool
results, partial events, and protocol metadata. Treat it as private diagnostic
and recovery material. Share the Markdown transcript by default, not the raw
journal. Do not delete either artifact without the user's approval.

## Output discipline

Every Claude and Codex debate turn, and the synthesis, must return a compressed
report of at most 40,000 output tokens. Preserve decisive arguments, proof
steps, counterexamples, qualifications, and verdicts. Put longer supporting
material in a project file and return its path.

## Workflow

1. Decide whether a two-model debate materially improves confidence. Use it for
   difficult, contested, proof-sensitive, or review-like tasks, not trivial
   lookups.
2. Prepare plain-text context files. Convert PDFs first, for example with
   `pdftotext -layout paper.pdf paper.txt`.
3. Write a prompt specifying the question, standard, assumptions, checks, and
   desired final format.
4. Run the bundled script with `--quiet`, explicit `--save` and
   `--progress` paths, and repeated `--context` flags.
5. Monitor the Markdown progress log and private JSONL stream journal.
6. Read the saved transcript. Report consensus if reached; otherwise report the
   unresolved split and both final positions.

## Examples

```bash
<skill-dir>/scripts/consensus --quiet \
  --save consensus_result.md \
  "Question to debate"
```

```bash
<skill-dir>/scripts/consensus --quiet --max-rounds 8 \
  --context paper.txt --context appendix.txt \
  --save consensus_review.md \
  --progress consensus_review.progress.md \
  < audit_prompt.txt
```

## Important flags

- `--max-rounds N`: maximum full Claude+Codex rounds; default 6.
- `--context FILE`: include a text file in every turn; repeatable.
- `--think TOKENS`: requested Claude thinking ceiling; default 42000.
- `--claude-tranche-think TOKENS`: per-call thinking cap; default 7000.
- `--claude-report-deadline SECS`: stream-activity check interval; default 300.
  The older `--claude-timeout` spelling is an alias.
- `--claude-recovery-timeout SECS`: no-thinking recovery deadline; default 120.
- `--codex-timeout SECS`: Codex per-turn deadline; default 1200; zero is unlimited.
- `--save FILE`: atomic Markdown transcript path.
- `--progress FILE`: fsynced Markdown event log path.
- `--no-synthesize`: use the last agreed turn rather than a fresh synthesis.

## Validation

After installation or modification, run:

```bash
python3 <skill-dir>/scripts/test_consensus.py
```

For implementation details, artifact semantics, and the tested failure modes,
read `references/OPERATIONS.md`.
