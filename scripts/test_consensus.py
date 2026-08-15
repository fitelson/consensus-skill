#!/usr/bin/env python3
"""Offline acceptance tests for the consensus runner."""

import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import tempfile
import textwrap
import time


RUNNER = Path(__file__).with_name("consensus")


FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time

mode = os.environ.get("FAKE_CLAUDE_MODE", "success")
recovery = os.environ.get("CLAUDE_CODE_DISABLE_THINKING") == "1"
prompt = sys.stdin.read()
is_synthesis = "FINAL AGREED ANSWER:" in prompt
pid_file = os.environ.get("FAKE_CLAUDE_PID_FILE")
if pid_file:
    with open(pid_file, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))

def answer(verdict, result):
    report = f"""CHECKPOINT
elapsed: 1 second
tentative_verdict: testing
new_results: {result}
current_obstruction: none
next_bounded_step: done
token_cost: unavailable
END CHECKPOINT

Mock Claude report.
VERDICT: {verdict}"""
    print(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": report}]},
    }), flush=True)

def plain(text):
    print(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    }), flush=True)

call_log = os.environ.get("FAKE_CLAUDE_CALL_LOG")
if call_log:
    with open(call_log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "argv": sys.argv[1:],
            "recovery": recovery,
            "synthesis": is_synthesis,
            "prompt_length": len(prompt),
            "thinking_tokens": os.environ.get("MAX_THINKING_TOKENS"),
            "thinking_disabled": os.environ.get("CLAUDE_CODE_DISABLE_THINKING") == "1",
        }) + "\n")

if is_synthesis and mode == "synthesis_success":
    plain("Mock synthesis success.")
elif is_synthesis and mode == "synthesis_recover" and not recovery:
    time.sleep(20)
elif is_synthesis and mode == "synthesis_recover" and recovery:
    plain("Mock synthesis recovered.")
elif is_synthesis and mode == "synthesis_fail":
    raise SystemExit(3)
elif mode == "timeout_recover" and not recovery:
    time.sleep(20)
elif mode == "activity_deadline" and not recovery:
    for n in range(100):
        print(json.dumps({"type": "system", "subtype": "status", "n": n}), flush=True)
        time.sleep(0.1)
elif mode == "leader_exits" and not recovery:
    child = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(30)"
    ])
    child_pid_file = os.environ.get("FAKE_CHILD_PID_FILE")
    if child_pid_file:
        with open(child_pid_file, "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))
    raise SystemExit(0)
elif mode == "interrupt" and not recovery:
    child = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(30)"
    ])
    child_pid_file = os.environ.get("FAKE_CHILD_PID_FILE")
    if child_pid_file:
        with open(child_pid_file, "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))
    while True:
        print(json.dumps({"type": "system", "subtype": "status"}), flush=True)
        time.sleep(0.1)
elif mode == "activity":
    for n in range(7):
        print(json.dumps({"type": "system", "subtype": "status", "n": n}), flush=True)
        time.sleep(0.7)
    answer("AGREE", "activity reset exercised")
elif mode == "stalled":
    answer("DISAGREE", "none")
elif mode == "disagree":
    answer("DISAGREE", "substantive continuing result")
else:
    answer("DISAGREE" if recovery else "AGREE", "checkpoint exercised")
'''


FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time

out = sys.argv[sys.argv.index("-o") + 1]
prompt = sys.stdin.read()
mode = os.environ.get("FAKE_CODEX_MODE", "success")
call_log = os.environ.get("FAKE_CODEX_CALL_LOG")
if call_log:
    with open(call_log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "argv": sys.argv[1:],
            "prompt_length": len(prompt),
        }) + "\n")
pid_file = os.environ.get("FAKE_CODEX_PID_FILE")
if pid_file:
    with open(pid_file, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
if mode == "leader_exits":
    child = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(30)"
    ])
    child_pid_file = os.environ.get("FAKE_CHILD_PID_FILE")
    if child_pid_file:
        with open(child_pid_file, "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))
    raise SystemExit(0)
if mode == "interrupt":
    child = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(30)"
    ])
    child_pid_file = os.environ.get("FAKE_CHILD_PID_FILE")
    if child_pid_file:
        with open(child_pid_file, "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))
    while True:
        time.sleep(0.1)
with open(out, "w", encoding="utf-8") as handle:
    verdict = "VERDICT: NOT AGREE" if mode == "malformed" else "VERDICT: AGREE"
    handle.write(f"Mock Codex report.\n{verdict}\n")
'''


def write_executable(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def assert_process_gone(pid):
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError(f"subprocess {pid} survived runner cleanup")


def run_case(root, fake_bin, name, mode, extra_args=None, *, synthesize=False,
             max_rounds=1, expected_code=0, extra_env=None):
    save = root / f"{name}.md"
    progress = root / f"{name}.progress.md"
    env = dict(os.environ)
    for key in (
        "CONSENSUS_CLAUDE_MODEL",
        "CONSENSUS_CLAUDE_EFFORT",
        "CONSENSUS_CODEX_MODEL",
        "CONSENSUS_CODEX_REASONING_EFFORT",
    ):
        env.pop(key, None)
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    env["FAKE_CLAUDE_MODE"] = mode
    if extra_env:
        env.update(extra_env)
    command = [
        str(RUNNER), "--quiet", "--max-rounds", str(max_rounds),
        "--claude-report-deadline", "1",
        "--claude-recovery-timeout", "2", "--save", str(save),
        "--progress", str(progress),
    ]
    if not synthesize:
        command.append("--no-synthesize")
    if extra_args:
        command.extend(extra_args)
    command.append(name)
    proc = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != expected_code:
        raise AssertionError(proc.stderr or proc.stdout)
    journal = Path(str(progress) + ".claude-stream.jsonl")
    rows = [json.loads(line) for line in journal.read_text().splitlines() if line]
    assert rows and rows[0]["type"] == "consensus_stream_journal"
    assert save.exists() and progress.exists()
    manifest = Path(str(progress) + ".protocol.json")
    protocol = json.loads(manifest.read_text(encoding="utf-8"))
    assert protocol["stream_activity_extends_absolute_deadline"] is False
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    return (
        progress.read_text(encoding="utf-8"),
        rows,
        transcript.read_text(encoding="utf-8") if (transcript := save).exists() else "",
    )


def main():
    with tempfile.TemporaryDirectory(prefix="consensus-skill-test-") as tmp:
        root = Path(tmp)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        write_executable(fake_bin / "claude", textwrap.dedent(FAKE_CLAUDE))
        write_executable(fake_bin / "codex", textwrap.dedent(FAKE_CODEX))

        success, _, _ = run_case(root, fake_bin, "success", "success")
        assert "Run completed" in success

        claude_log = root / "neutral-claude.jsonl"
        codex_log = root / "neutral-codex.jsonl"
        run_case(
            root,
            fake_bin,
            "model-neutrality",
            "success",
            extra_env={
                "FAKE_CLAUDE_CALL_LOG": str(claude_log),
                "FAKE_CODEX_CALL_LOG": str(codex_log),
            },
        )
        neutral_claude_args = json.loads(claude_log.read_text().splitlines()[0])["argv"]
        neutral_codex_args = json.loads(codex_log.read_text().splitlines()[0])["argv"]
        assert "--model" not in neutral_claude_args
        assert "--effort" not in neutral_claude_args
        assert "--model" not in neutral_codex_args

        claude_log = root / "override-claude.jsonl"
        codex_log = root / "override-codex.jsonl"
        run_case(
            root,
            fake_bin,
            "model-overrides",
            "success",
            extra_env={
                "FAKE_CLAUDE_CALL_LOG": str(claude_log),
                "FAKE_CODEX_CALL_LOG": str(codex_log),
                "CONSENSUS_CLAUDE_MODEL": "mock-claude",
                "CONSENSUS_CLAUDE_EFFORT": "high",
                "CONSENSUS_CODEX_MODEL": "mock-codex",
                "CONSENSUS_CODEX_REASONING_EFFORT": "high",
            },
        )
        override_claude_args = json.loads(claude_log.read_text().splitlines()[0])["argv"]
        override_codex_args = json.loads(codex_log.read_text().splitlines()[0])["argv"]
        assert override_claude_args[override_claude_args.index("--model") + 1] == "mock-claude"
        assert override_claude_args[override_claude_args.index("--effort") + 1] == "high"
        assert override_codex_args[override_codex_args.index("--model") + 1] == "mock-codex"
        assert 'model_reasoning_effort="high"' in override_codex_args

        invalid_progress, _, invalid_transcript = run_case(
            root,
            fake_bin,
            "invalid-codex-verdict",
            "success",
            expected_code=1,
            extra_env={"FAKE_CODEX_MODE": "malformed"},
        )
        assert "Codex invalid report 1/2" in invalid_progress
        assert "Codex invalid report 2/2" in invalid_progress
        assert "ABORTED" in invalid_transcript

        recovery_log = root / "research-recovery-calls.jsonl"
        recovery, _, _ = run_case(
            root,
            fake_bin,
            "recovery",
            "timeout_recover",
            extra_env={"FAKE_CLAUDE_CALL_LOG": str(recovery_log)},
        )
        assert "silent 5/5" in recovery
        assert "terminated after five silent intervals" in recovery
        assert "recovery: yes" in recovery
        recovery_calls = [
            json.loads(line) for line in recovery_log.read_text().splitlines()
        ]
        initial_call, recovered_call = recovery_calls[0], recovery_calls[1]
        initial_args, recovered_args = initial_call["argv"], recovered_call["argv"]
        research_session = initial_args[initial_args.index("--session-id") + 1]
        assert recovered_args[recovered_args.index("--resume") + 1] == research_session
        assert initial_call["thinking_tokens"] == "7000"
        assert initial_call["thinking_disabled"] is False
        assert recovered_call["thinking_tokens"] is None
        assert recovered_call["thinking_disabled"] is True

        activity, rows, _ = run_case(root, fake_bin, "activity", "activity")
        assert "stream activity observed" in activity
        assert "terminated after five silent intervals" not in activity
        assert len(rows) > 2

        hard_cap, _, _ = run_case(
            root,
            fake_bin,
            "activity-deadline",
            "activity_deadline",
            extra_args=["--claude-turn-timeout", "2"],
        )
        assert "reached absolute turn deadline" in hard_cap
        assert "stream_activity_extended_deadline: no" in hard_cap
        assert "recovery: yes" in hard_cap

        runner_symbols = runpy.run_path(str(RUNNER))
        verdict_of = runner_symbols["verdict_of"]
        assert verdict_of("x\nVERDICT: AGREE\n") == "AGREE"
        assert verdict_of("x\nVERDICT: DISAGREE\n") == "DISAGREE"
        assert verdict_of("VERDICT: NOT AGREE") is None
        assert verdict_of("VERDICT: DISAGREEMENT") is None
        assert verdict_of("VERDICT: AGREE\ntrailing material") is None

        call_log = root / "synthesis-calls.jsonl"
        synth_progress, _, synth_transcript = run_case(
            root,
            fake_bin,
            "synthesis-success",
            "synthesis_success",
            synthesize=True,
            extra_env={"FAKE_CLAUDE_CALL_LOG": str(call_log)},
        )
        assert "Claude synthesis completed" in synth_progress
        assert "Mock synthesis success." in synth_transcript

        call_log.write_text("", encoding="utf-8")
        recovered_progress, _, recovered_transcript = run_case(
            root,
            fake_bin,
            "synthesis-recovered",
            "synthesis_recover",
            extra_args=["--claude-turn-timeout", "2"],
            synthesize=True,
            extra_env={"FAKE_CLAUDE_CALL_LOG": str(call_log)},
        )
        assert "Claude synthesis recovered" in recovered_progress
        assert "Mock synthesis recovered." in recovered_transcript
        calls = [json.loads(line) for line in call_log.read_text().splitlines()]
        synth_calls = [call for call in calls if call["synthesis"]]
        assert len(synth_calls) == 2
        first_args, recovered_args = synth_calls[0]["argv"], synth_calls[1]["argv"]
        session_id = first_args[first_args.index("--session-id") + 1]
        assert recovered_args[recovered_args.index("--resume") + 1] == session_id

        failed_progress, _, failed_transcript = run_case(
            root,
            fake_bin,
            "synthesis-fallback",
            "synthesis_fail",
            synthesize=True,
        )
        assert "synthesis recovery failed" in failed_progress
        assert "Mock Codex report." in failed_transcript

        stalled_progress, _, stalled_transcript = run_case(
            root,
            fake_bin,
            "stalled",
            "stalled",
            max_rounds=2,
        )
        assert "Debate stalled" in stalled_progress
        assert "NO CONSENSUS — STALLED" in stalled_transcript
        assert stalled_transcript.count("## Claude") == 2

        budget_log = root / "aggregate-thinking-calls.jsonl"
        run_case(
            root,
            fake_bin,
            "aggregate-thinking-budget",
            "disagree",
            max_rounds=6,
            extra_env={"FAKE_CLAUDE_CALL_LOG": str(budget_log)},
        )
        budget_calls = [
            json.loads(line) for line in budget_log.read_text().splitlines()
        ]
        fresh_allocations = [
            int(call["thinking_tokens"])
            for call in budget_calls
            if call["thinking_tokens"] is not None
        ]
        assert sum(fresh_allocations) == 42000
        assert all(allocation <= 7000 for allocation in fresh_allocations)

        large_context = root / "large-context.txt"
        large_context.write_text("x" * 1_200_000, encoding="utf-8")
        _, _, _ = run_case(
            root,
            fake_bin,
            "large-stdin-prompt",
            "success",
            extra_args=["--context", str(large_context)],
        )

        # Terminating the runner must reap the active model process group and
        # leave an INTERRUPTED transcript, even while the model is streaming.
        save = root / "interrupted.md"
        progress = root / "interrupted.progress.md"
        pid_file = root / "interrupted-model.pid"
        child_pid_file = root / "interrupted-child.pid"
        env = dict(os.environ)
        env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_CLAUDE_MODE"] = "interrupt"
        env["FAKE_CLAUDE_PID_FILE"] = str(pid_file)
        env["FAKE_CHILD_PID_FILE"] = str(child_pid_file)
        command = [
            str(RUNNER), "--quiet", "--max-rounds", "1", "--no-synthesize",
            "--claude-report-deadline", "1", "--claude-turn-timeout", "30",
            "--save", str(save), "--progress", str(progress),
            "interruption cleanup",
        ]
        running = subprocess.Popen(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(100):
            if ((pid_file.exists() and child_pid_file.exists())
                    or running.poll() is not None):
                break
            time.sleep(0.05)
        assert pid_file.exists(), "fake Claude process never started"
        assert child_pid_file.exists(), "fake Claude child never started"
        model_pid = int(pid_file.read_text(encoding="utf-8"))
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        running.terminate()
        stdout, stderr = running.communicate(timeout=10)
        assert running.returncode == 130, (stdout, stderr)
        assert "INTERRUPTED" in save.read_text(encoding="utf-8")
        assert_process_gone(model_pid)
        assert_process_gone(child_pid)

        # Regression for the original race: the group leader exits normally
        # before cleanup while its descendant remains alive.
        child_pid_file = root / "claude-leader-exits-child.pid"
        run_case(
            root,
            fake_bin,
            "claude-leader-exits",
            "leader_exits",
            extra_env={"FAKE_CHILD_PID_FILE": str(child_pid_file)},
        )
        assert child_pid_file.exists()
        assert_process_gone(int(child_pid_file.read_text(encoding="utf-8")))

        child_pid_file = root / "codex-leader-exits-child.pid"
        run_case(
            root,
            fake_bin,
            "codex-leader-exits",
            "success",
            expected_code=1,
            extra_env={
                "FAKE_CODEX_MODE": "leader_exits",
                "FAKE_CHILD_PID_FILE": str(child_pid_file),
            },
        )
        assert child_pid_file.exists()
        assert_process_gone(int(child_pid_file.read_text(encoding="utf-8")))

        # The same process-group guarantee applies when Codex is active.
        save = root / "interrupted-codex.md"
        progress = root / "interrupted-codex.progress.md"
        pid_file = root / "interrupted-codex-model.pid"
        child_pid_file = root / "interrupted-codex-child.pid"
        env = dict(os.environ)
        env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_CLAUDE_MODE"] = "success"
        env["FAKE_CODEX_MODE"] = "interrupt"
        env["FAKE_CODEX_PID_FILE"] = str(pid_file)
        env["FAKE_CHILD_PID_FILE"] = str(child_pid_file)
        command = [
            str(RUNNER), "--first", "codex", "--quiet", "--max-rounds", "1",
            "--no-synthesize", "--save", str(save), "--progress", str(progress),
            "Codex interruption cleanup",
        ]
        running = subprocess.Popen(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(100):
            if ((pid_file.exists() and child_pid_file.exists())
                    or running.poll() is not None):
                break
            time.sleep(0.05)
        assert pid_file.exists(), "fake Codex process never started"
        assert child_pid_file.exists(), "fake Codex child never started"
        model_pid = int(pid_file.read_text(encoding="utf-8"))
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        running.terminate()
        stdout, stderr = running.communicate(timeout=10)
        assert running.returncode == 130, (stdout, stderr)
        assert "INTERRUPTED" in save.read_text(encoding="utf-8")
        assert_process_gone(model_pid)
        assert_process_gone(child_pid)

    print("PASS: consensus skill offline acceptance tests")


if __name__ == "__main__":
    main()
