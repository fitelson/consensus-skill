#!/usr/bin/env python3
"""Offline acceptance tests for the portable consensus runner."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap


RUNNER = Path(__file__).with_name("consensus")


FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json
import os
import time

mode = os.environ.get("FAKE_CLAUDE_MODE", "success")
recovery = os.environ.get("CLAUDE_CODE_DISABLE_THINKING") == "1"

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

if mode == "timeout_recover" and not recovery:
    time.sleep(20)
elif mode == "activity":
    for n in range(7):
        print(json.dumps({"type": "system", "subtype": "status", "n": n}), flush=True)
        time.sleep(0.7)
    answer("AGREE", "activity reset exercised")
else:
    answer("DISAGREE" if recovery else "AGREE", "checkpoint exercised")
'''


FAKE_CODEX = r'''#!/usr/bin/env python3
import sys

out = sys.argv[sys.argv.index("-o") + 1]
with open(out, "w", encoding="utf-8") as handle:
    handle.write("Mock Codex report.\nVERDICT: AGREE\n")
'''


def write_executable(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def run_case(root, fake_bin, name, mode):
    save = root / f"{name}.md"
    progress = root / f"{name}.progress.md"
    env = dict(os.environ)
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    env["FAKE_CLAUDE_MODE"] = mode
    proc = subprocess.run(
        [
            str(RUNNER), "--quiet", "--max-rounds", "1",
            "--no-synthesize", "--claude-report-deadline", "1",
            "--claude-recovery-timeout", "2", "--save", str(save),
            "--progress", str(progress), name,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    journal = Path(str(progress) + ".claude-stream.jsonl")
    rows = [json.loads(line) for line in journal.read_text().splitlines() if line]
    assert rows and rows[0]["type"] == "consensus_stream_journal"
    assert save.exists() and progress.exists()
    return progress.read_text(encoding="utf-8"), rows


def main():
    with tempfile.TemporaryDirectory(prefix="consensus-portable-test-") as tmp:
        root = Path(tmp)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        write_executable(fake_bin / "claude", textwrap.dedent(FAKE_CLAUDE))
        write_executable(fake_bin / "codex", textwrap.dedent(FAKE_CODEX))

        success, _ = run_case(root, fake_bin, "success", "success")
        assert "Run completed" in success

        recovery, _ = run_case(root, fake_bin, "recovery", "timeout_recover")
        assert "missed 5/5" in recovery
        assert "terminated after five silent intervals" in recovery
        assert "recovery: yes" in recovery

        activity, rows = run_case(root, fake_bin, "activity", "activity")
        assert "stream activity observed" in activity
        assert "terminated after five silent intervals" not in activity
        assert len(rows) > 2

    print("PASS: portable consensus offline acceptance tests")


if __name__ == "__main__":
    main()
