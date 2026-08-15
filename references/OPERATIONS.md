# Operations and recovery semantics

## Artifacts

For `--save debate.md --progress debate.progress.md`, the runner maintains:

- `debate.md`: atomic Markdown transcript and final synthesis;
- `debate.progress.md`: fsynced human-readable event/status log;
- `debate.progress.md.claude-stream.jsonl`: private fsynced raw Claude stream
  journal.
- `debate.progress.md.protocol.json`: runner identity, protocol version, and
  enforced deadlines.

The transcript is rewritten atomically after every completed participant turn.
Temporary content and the destination directory are fsynced before the update
is reported complete.
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

Stream activity is liveness evidence only. It never extends the independent
900-second absolute cap on a live Claude research turn or synthesis. Reaching
that cap terminates the process and resumes the same session for bounded
recovery.

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
session. Synthesis shares the stream journal and silence monitor. If its live
process fails, the runner makes one bounded no-thinking recovery call using the
same synthesis session ID. Only if recovery also fails does it use the last
agreed turn.

Only an exact verdict on the final nonempty line is accepted. `AGREE` means
agreement on the best-supported answer, including agreement that
a proposition or research question is presently unresolved; it does not mean
that the proposition under investigation was proved. Two consecutive completed
Claude `DISAGREE` checkpoints that explicitly report
no substantive new result also stop the debate.
The second completed no-progress turn remains in the transcript, and the run
ends normally as `NO CONSENSUS — STALLED`, not as an abort.

## Privacy

The raw JSONL journal may contain:

- full prompts and supplied documents;
- tool calls and tool results;
- partial stream events and protocol metadata;
- model-specific opaque signatures.

Treat it as private diagnostic/recovery material. Do not publish it without
review. The Markdown transcript is the intended shareable record.

The journal is created with mode `0600`. Prompts and context are delivered to
both model CLIs through stdin, avoiding command-line disclosure and operating
system argument-size limits. Persisted model-failure diagnostics contain exit
status and protocol metadata, not raw prompt-bearing stdout or stderr.

## Model neutrality

The runner does not pass `--model` or effort flags unless the optional
`CONSENSUS_*` environment variables are present. This lets the recipient's
authenticated Claude and Codex installations select their own supported
defaults.

The `--think` budget is aggregate across fresh Claude research turns and the
initial synthesis call. Each call is separately capped by
`--claude-tranche-think`; recovery calls disable thinking and consume none of
the aggregate budget.

## Tested failure modes

The bundled offline tests cover:

- ordinary two-turn agreement;
- five silent intervals followed by same-session recovery;
- streamed activity resetting the silence count;
- continuing streamed activity not extending the absolute turn deadline;
- interruption and leader-exits-first cleanup reaping the complete active
  model process group;
- strict verdict parsing and invalid Codex report handling;
- normal stalled termination retaining both completed turns;
- synthesis success, same-session recovery, and agreed-turn fallback;
- private journal permissions and prompts larger than the command-line limit;
- recipient defaults and explicit model overrides;
- valid JSONL journaling;
- atomic transcript/progress/manifest creation.

The design was also acceptance-tested on a multi-file proof/report audit. That
test exposed and led to fixes for (i) a timeout-boundary response that existed
in Claude's persisted session but was not harvested, (ii) overly broad edit
permission for a Claude-written log, and (iii) a `None` journal-path error in
the synthesis step. The current architecture uses runner-owned journaling,
read-only checkpointing, resumable session IDs, and a synthesis fallback.
