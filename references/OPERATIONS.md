# Operations and recovery semantics

## Artifacts

For `--save debate.md --progress debate.progress.md`, the runner maintains:

- `debate.md`: atomic Markdown transcript and final synthesis;
- `debate.progress.md`: fsynced human-readable event/status log;
- `debate.progress.md.claude-stream.jsonl`: private fsynced raw Claude stream
  journal.

The transcript is rewritten atomically after every completed participant turn.
The event log records session IDs, activity/miss events, recoveries, completed
turns, synthesis, and terminal status. The JSONL journal records each streamed
stdout/stderr line with a timestamp and stream label.

## Five-interval policy

The default activity interval is 300 seconds. At each boundary:

- new journal activity resets the silence count;
- no activity adds one strike;
- five consecutive silent intervals terminate the live Claude process;
- the runner then resumes the same Claude session for a no-thinking recovery
  report, rather than starting the research over.

The interval is configurable with `--claude-report-deadline`; its older
`--claude-timeout` name remains an alias. The recovery deadline is separately
controlled by `--claude-recovery-timeout`.

## Returned checkpoint validation

A completed Claude research report must contain:

```text
CHECKPOINT
elapsed: ...
tentative_verdict: ...
new_results: ...
current_obstruction: ...
next_bounded_step: ...
token_cost: ...
END CHECKPOINT
...
VERDICT: AGREE|DISAGREE
```

Stream activity and returned-report validity are distinct. Activity prevents
premature termination; the completed response must still pass schema and
verdict validation.

## Consensus and synthesis

Consensus requires two consecutive completed turns with `VERDICT: AGREE`.
After consensus, Claude produces a clean final synthesis in a new stable
session. Synthesis shares the stream journal and silence monitor. If it fails,
the runner preserves and uses the last agreed turn.

Two consecutive completed Claude `DISAGREE` checkpoints that explicitly report
no substantive new result also stop the debate.

## Privacy

The raw JSONL journal may contain:

- full prompts and supplied documents;
- tool calls and tool results;
- partial stream events and protocol metadata;
- model-specific opaque signatures.

Treat it as private diagnostic/recovery material. Do not publish it without
review. The Markdown transcript is the intended shareable record.

## Model neutrality

The portable runner does not pass `--model` or effort flags unless the optional
`CONSENSUS_*` environment variables are present. This lets the recipient's
authenticated Claude and Codex installations select their own supported
defaults.

## Tested failure modes

The bundled offline tests cover:

- ordinary two-turn agreement;
- five silent intervals followed by same-session recovery;
- streamed activity resetting the silence count;
- valid JSONL journaling;
- atomic transcript/progress creation.

The design was also acceptance-tested on a multi-file proof/report audit. That
test exposed and led to fixes for (i) a timeout-boundary response that existed
in Claude's persisted session but was not harvested, (ii) overly broad edit
permission for a Claude-written log, and (iii) a `None` journal-path error in
the synthesis step. The current architecture uses runner-owned journaling,
read-only checkpointing, resumable session IDs, and a synthesis fallback.
