#!/usr/bin/env python3
"""Bounded, offline lifecycle regressions for the adjacent consensus runner.

Run with ``python3 -B scripts/test_lifecycle.py -v``. Each case has an outer
subprocess watchdog, private temporary fixtures, and an exact-PID ownership
ledger. Watchdog cleanup never signals a process-name match or a process group.
Only the runner under test performs its normal owned-group cleanup.
"""

import ast
import base64
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock


RUNNER = Path(__file__).with_name("consensus")
SELF = Path(__file__).resolve()
# Allow fake CLI startup before exercising the EOF deadline on busy machines.
CALL_CAP = 2.0
WATCHDOG = 20
TAIL = "LIFECYCLE_SIGTERM_TAIL"
BURST_STDOUT = b'{"type":"assistant","text":"' + b"x" * 196608
BURST_STDERR = bytes(range(256)) * 16


FAKE_CLI = r'''
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

root = Path(os.environ["LIFECYCLE_CASE"])
mode = os.environ["LIFECYCLE_MODE"]
provider = Path(sys.argv[0]).name
args = sys.argv[1:]
child = None

def append(name, record):
    descriptor = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, (json.dumps(record) + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)

if "--descendant" in args:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    (root / "descendant-ready").write_text("ready", encoding="utf-8")
    time.sleep(60)
    sys.exit(0)

def terminate(_signum, _frame):
    (root / "term-received").write_text("received", encoding="utf-8")
    if mode == "resist":
        return
    if mode == "tail":
        os.write(1, b'{"tail":"LIFECYCLE_SIGTERM_TAIL"}\n')
        os.write(2, b'LIFECYCLE_SIGTERM_TAIL_STDERR\n')
    if mode == "semantic-tail":
        report = "CHECKPOINT\nelapsed: 1s\ntentative_verdict: AGREE\nnew_results: NEW: LIFECYCLE_SIGTERM_TAIL\ncurrent_obstruction: none\nnext_bounded_step: done\ntoken_cost: unavailable\nEND CHECKPOINT"
        print(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": report}
        ]}}), flush=True)
    if child is not None:
        # The runner must signal the group. Waiting here does not signal the
        # descendant itself and ensures it is reaped before the leader exits.
        child.wait(timeout=2)
    raise SystemExit(0)

signal.signal(signal.SIGTERM, terminate)
if mode == "startup":
    child = subprocess.Popen([sys.executable, __file__, "--descendant"])
    append("owned-pids.jsonl", {"pid": child.pid, "role": "descendant"})
    deadline = time.monotonic() + 3
    while not (root / "descendant-ready").exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("descendant failed to start")
        time.sleep(0.01)

(root / "fixture-ready").write_text(str(os.getpid()), encoding="utf-8")
if mode in {"startup", "resist", "tail", "semantic-tail"}:
    time.sleep(60)
    sys.exit(0)
if mode in {"raw-flood", "capture-flood", "mixed-flood"}:
    chunk = b"x" * 4096  # Deliberately no newline: never depend on readline.
    while True:
        os.write(1, chunk)
        if mode == "mixed-flood":
            os.write(2, chunk)
        time.sleep(0.005)

prompt = sys.stdin.read()
if mode == "observer-burst":
    # Finish a bounded burst before the observer is allowed to reject it.
    for descriptor, payload in (
        (1, b'{"type":"assistant","text":"' + b"x" * 196608),
        (2, bytes(range(256)) * 16),
    ):
        remaining = memoryview(payload)
        while remaining:
            remaining = remaining[os.write(descriptor, remaining):]
    (root / "writer-completed").write_text("done", encoding="utf-8")
    sys.exit(0)
counter_path = root / (provider + ".count")
count = int(counter_path.read_text()) + 1 if counter_path.exists() else 1
counter_path.write_text(str(count), encoding="utf-8")
resumed = "--resume" in args if provider == "claude" else "resume" in args
if provider == "claude":
    flag = "--resume" if resumed else "--session-id"
    session = args[args.index(flag) + 1]
else:
    session = args[-2] if resumed else "11111111-1111-4111-8111-111111111111"
append("calls.jsonl", {
    "provider": provider, "pid": os.getpid(), "session": session,
    "resumed": resumed, "call": count,
    "recovery": "EXTERNAL RECOVERY TRANCHE" in prompt,
    "thinking_disabled": os.environ.get("CLAUDE_CODE_DISABLE_THINKING") == "1",
    "argv": args,
})
if provider == "codex":
    if mode == "healthy-tail" and not resumed:
        os.write(2, b"startup before queued controls\n")
        deadline = time.monotonic() + 4
        while not (root / "startup-journal-blocked").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("startup journal did not reach the handshake")
            time.sleep(0.01)
    print(json.dumps({"type": "thread.started", "thread_id": session}), flush=True)

if mode == "eof" or (mode == "cap-recovery" and count == 1):
    os.close(1)
    os.close(2)
    (root / "pipes-closed").write_text("closed", encoding="utf-8")
    time.sleep(60)
    sys.exit(0)

verdict = "DISAGREE" if mode == "tranches" and count == 1 else "AGREE"
if mode == "final-disagree" and provider == "codex":
    verdict = "DISAGREE"
report = (
    "CHECKPOINT\nelapsed: 1 second\ntentative_verdict: " + verdict +
    "\nnew_results: NEW: verified " + provider + " tranche " + str(count) +
    "\ncurrent_obstruction: none\nnext_bounded_step: done\n"
    "token_cost: unavailable\nEND CHECKPOINT\n\n"
    "The finite counterexample refutes the universal claim.\nVERDICT: " + verdict
)
if mode == "invalid-recovery" and count == 1:
    report = "A partial report without its checkpoint.\nVERDICT: AGREE"
if provider == "claude":
    print(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": report},
    ]}}), flush=True)
    print(json.dumps({"type": "result", "subtype": "success",
                      "is_error": False, "result": report}), flush=True)
else:
    if mode == "healthy-tail" and not resumed:
        print(json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": report,
        }}), flush=True)
        time.sleep(60)
        sys.exit(0)
    output = Path(args[args.index("-o") + 1])
    if mode == "fifo":
        output.unlink()  # Only the runner's disposable fake-provider output.
        os.mkfifo(output, 0o600)
        (root / "fifo-created").write_text(str(output), encoding="utf-8")
    else:
        output.write_text(report, encoding="utf-8")
    print(json.dumps({"type": "item.completed", "item": {
        "type": "agent_message", "text": report,
    }}), flush=True)
    if mode == "live-control":
        (root / "signal-ready").write_text("active provider", encoding="utf-8")
        time.sleep(60)
    print(json.dumps({"type": "turn.completed"}), flush=True)
'''


def append_record(path, record):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, (json.dumps(record) + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)


def read_records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def wait_for_file(path, proc, deadline):
    while time.monotonic() < deadline:
        if path.exists():
            return True
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(0.01)
    return False


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def kill_owned_pid(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def load_runner():
    module = types.ModuleType("consensus_lifecycle_runtime")
    module.__file__ = str(RUNNER.resolve())
    sys.modules[module.__name__] = module
    with mock.patch("urllib.request.urlopen",
                    side_effect=AssertionError("offline test attempted network access")):
        exec(compile(RUNNER.read_bytes(), str(RUNNER), "exec"), module.__dict__)
    return module


def streaming_finally_boundary():
    """Find the actual cleanup call, not an earlier leader-exit cleanup."""
    tree = ast.parse(RUNNER.read_bytes(), filename=str(RUNNER))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "_run_streaming")
    calls = []
    for statement in function.body:
        if isinstance(statement, ast.Try):
            for final in statement.finalbody:
                calls.extend(node for node in ast.walk(final)
                             if isinstance(node, ast.Call)
                             and isinstance(node.func, ast.Name)
                             and node.func.id == "_terminate_process_group")
    if len(calls) != 1:
        raise AssertionError("expected one _run_streaming finally cleanup call")
    return calls[0].lineno, calls[0].end_lineno


def worker(action):
    """One isolated runner invocation; the parent owns the outer deadline."""
    root = Path(os.environ["LIFECYCLE_CASE"])
    runner = load_runner()
    runner.urlopen = mock.Mock(side_effect=AssertionError("network access forbidden"))
    stats = {
        "action": action, "queue_peak": 0, "queue_capacity": None,
        "max_chunk_bytes": 0, "observer_bytes": 0, "tail_read": False,
        "default_capture_limit": runner.CAPTURE_MAX_BYTES,
        "default_raw_limit": runner.STREAM_MAX_BYTES, "interrupts": 0,
    }
    original_popen = subprocess.Popen
    original_start = threading.Thread.start
    original_queue = queue.Queue
    original_cleanup = runner._terminate_process_group
    original_codex = runner.run_codex
    original_killpg = os.killpg
    original_fsync = os.fsync
    owned_processes = []
    reader_eof = set()
    readers_finished = threading.Event()
    queued_controls = threading.Event()
    main_stop = action == "main-stop"
    mode = os.environ["LIFECYCLE_MODE"]
    boundary = streaming_finally_boundary() if main_stop or action == "codex-fifo-stop" else None

    def await_external_stop(label):
        if runner.shutdown_requested():
            raise AssertionError("stop was already latched before handshake")
        stats["signal_boundary"] = label
        (root / "signal-ready").write_text(label, encoding="utf-8")
        deadline = time.monotonic() + 4
        while not runner.shutdown_requested() and time.monotonic() < deadline:
            time.sleep(0.005)
        if not runner.shutdown_requested():
            raise AssertionError("external SIGTERM did not reach the shutdown latch")

    def boundary_cleanup(proc, pgid):
        original_cleanup(proc, pgid)
        caller = sys._getframe(1)
        if not boundary or caller.f_code.co_name != "_run_streaming" \
                or not boundary[0] <= caller.f_lineno <= boundary[1]:
            return
        stats["successful_cleanups"] = stats.get("successful_cleanups", 0) + 1
        target = 1 if action == "codex-fifo-stop" else (3 if mode == "synthesis-cleanup" else 2)
        if mode in {"live-control", "research-oserror"} or stats["successful_cleanups"] != target:
            return
        if proc.returncode != 0:
            raise AssertionError("cleanup boundary was not a successful provider exit")
        stats["boundary_provider_returncode"] = proc.returncode
        await_external_stop("successful streaming finally cleanup")

    def research_oserror(*args, **kwargs):
        if action in {"healthy-tail", "main-tail"} and not kwargs.get("session", {}).get("id"):
            with mock.patch.object(os, "fsync", side_effect=delay_startup_journal):
                result = original_codex(*args, **kwargs)
        else:
            result = original_codex(*args, **kwargs)
        if main_stop and mode == "research-oserror":
            await_external_stop("completed research transport before injected OSError")
            stats["injected_research_oserror"] = True
            raise OSError("injected research I/O failure after SIGTERM")
        return result

    def delay_startup_journal(descriptor):
        if not stats.get("delayed_startup_journal"):
            stats["delayed_startup_journal"] = True
            (root / "startup-journal-blocked").write_text("blocked", encoding="utf-8")
            if not queued_controls.wait(4):
                raise AssertionError("session and checkpoint did not reach the queue")
            # Expire the live-call cap with controls queued and a healthy observer.
            time.sleep(1.2 if action == "main-tail" else 0.7)
        return original_fsync(descriptor)

    def recorded_killpg(pgid, sig):
        if action == "cleanup-stop" and sig in {signal.SIGTERM, signal.SIGKILL}:
            stats.setdefault("group_signals", []).append({
                "pgid": pgid, "signal": int(sig), "time": time.monotonic(),
                "stopping": runner.shutdown_requested(),
            })
        return original_killpg(pgid, sig)

    def recorded_popen(command, *args, **kwargs):
        executable = command[0]
        if executable in {"claude", "codex"}:
            resolved = shutil.which(executable, path=os.environ["PATH"])
            if resolved != str(root / "bin" / executable):
                raise AssertionError("attempt to launch a nonfixture provider")
        elif executable != sys.executable or command[1] != str(root / "bin" / "fixture"):
            raise AssertionError("unexpected child executable")
        proc = original_popen(command, *args, **kwargs)
        owned_processes.append(proc)
        append_record(root / "owned-pids.jsonl", {"pid": proc.pid, "role": "provider"})
        return proc

    class RecordingQueue(original_queue):
        def __init__(self, maxsize=0):
            super().__init__(maxsize=maxsize)
            stats["queue_capacity"] = maxsize

        def put(self, item, *args, **kwargs):
            if isinstance(item, tuple) and isinstance(item[1], bytes):
                stats["max_chunk_bytes"] = max(stats["max_chunk_bytes"], len(item[1]))
                if TAIL.encode("ascii") in item[1]:
                    stats["tail_read"] = True
                    time.sleep(0.15)  # Tail reaches the queue after termination.
            super().put(item, *args, **kwargs)
            stats["queue_peak"] = max(stats["queue_peak"], self.qsize())
            if action in {"healthy-tail", "main-tail"} and isinstance(item, tuple) \
                    and item[0] == "stdout" and isinstance(item[1], bytes):
                if b"thread.started" in item[1]:
                    stats["queued_session"] = True
                if b"END CHECKPOINT" in item[1]:
                    stats["queued_checkpoint"] = True
                if stats.get("queued_session") and stats.get("queued_checkpoint"):
                    queued_controls.set()
            if action == "observer-overflow" and isinstance(item, tuple) and item[1] is None:
                reader_eof.add(item[0])
                if len(reader_eof) == 2:
                    readers_finished.set()

    starts = 0

    def injected_start(thread):
        nonlocal starts
        starts += 1
        if starts == int(os.environ.get("LIFECYCLE_FAIL_START", "0")):
            if not wait_for_file(root / "fixture-ready", None, time.monotonic() + 4):
                raise RuntimeError("fixture failed to become ready before injection")
            stats["injected_thread_start"] = starts
            raise RuntimeError("injected Thread.start failure")
        return original_start(thread)

    def interrupt(_signum, _frame):
        stats["interrupts"] += 1
        raise KeyboardInterrupt

    def observe(_stream, text):
        stats["observer_bytes"] += len(text.encode("utf-8"))

    observer = None
    if action == "observer-overflow":
        runner.EVENT_MAX_BYTES = 1024
        observer = runner.stream_observer("Claude")

    def overflow_observer(stream, text):
        stats["observer_calls"] = stats.get("observer_calls", 0) + 1
        if stats.get("observer_failed"):
            stats["observer_calls_after_failure"] = stats.get("observer_calls_after_failure", 0) + 1
        if stream == "stdout" and not stats.get("burst_retained_before_failure"):
            deadline = time.monotonic() + 4
            if not wait_for_file(root / "writer-completed", None, deadline) \
                    or not readers_finished.wait(max(0, deadline - time.monotonic())):
                raise AssertionError("writer burst was not fully retained before observer failure")
            owned_processes[0].wait(timeout=max(0.01, deadline - time.monotonic()))
            stats["burst_retained_before_failure"] = True
        try:
            observer(stream, text)
        except runner.StreamLimitExceeded:
            stats["observer_failed"] = True
            raise

    if action in {"cleanup-stop", "codex-fifo-stop"}:
        runner.install_shutdown_handlers()
    else:
        signal.signal(signal.SIGTERM, interrupt)
    started = time.monotonic()
    monitor = None
    try:
        with mock.patch.object(subprocess, "Popen", recorded_popen), \
                mock.patch.object(queue, "Queue", RecordingQueue), \
                mock.patch.object(threading.Thread, "start", injected_start), \
                mock.patch.object(runner, "_terminate_process_group", boundary_cleanup), \
                mock.patch.object(runner, "run_codex", research_oserror), \
                mock.patch.object(os, "killpg", recorded_killpg):
            if action in {"main", "main-tail"} or main_stop:
                cap = "1" if action == "main-tail" else (
                    "2" if os.environ["LIFECYCLE_MODE"] == "cap-recovery" else "5")
                sys.argv = [
                    str(RUNNER), "--quiet", "--max-rounds", "1" if main_stop else "2",
                    "--claude-turn-timeout", cap, "--codex-timeout", cap,
                    "--claude-recovery-timeout", "5", "--save", str(root / "debate.md"),
                    "--progress", str(root / "progress.md"), "offline lifecycle fixture",
                ]
                if not main_stop or mode not in {"synthesis-cleanup", "pending-synthesis"}:
                    sys.argv.insert(1, "--no-synthesize")
                stats["main_return"] = runner.main()
            elif action == "healthy-tail":
                session = {}
                stats["tail_session"] = session
                stats["tail_checkpoints"] = []
                research_oserror(
                    "offline fixture", timeout=0.5, session=session,
                    stream_journal=root / "journal.jsonl",
                    checkpoint_report=stats["tail_checkpoints"].append,
                )
            elif action in {"claude-eof", "codex-eof", "codex-fifo", "codex-fifo-stop"}:
                resumed = os.environ.get("LIFECYCLE_RESUME") == "1"
                session = "11111111-1111-4111-8111-111111111111"
                if action == "claude-eof":
                    runner.run_claude(
                        "offline fixture", 0 if resumed else 100, timeout=CALL_CAP,
                        session_id=session, resume=resumed, stream_journal=root / "journal.jsonl",
                    )
                else:
                    runner.run_codex(
                        "offline fixture", timeout=CALL_CAP,
                        session={"id": session} if resumed else {},
                        stream_journal=root / "journal.jsonl",
                    )
                stats["returned"] = True
            else:
                if action == "raw-limit":
                    runner.STREAM_MAX_BYTES = 131072
                    runner.CAPTURE_MAX_BYTES = 524288
                elif action == "capture-limit":
                    runner.CAPTURE_MAX_BYTES = 65536
                stats["capture_limit"] = runner.CAPTURE_MAX_BYTES
                stats["raw_limit"] = runner.STREAM_MAX_BYTES
                timeout = 15 if action in {"repeated-signal", "interrupt-tail"} else (
                    2 if action.endswith("limit") else CALL_CAP)
                if action == "semantic-stop":
                    monitor = runner.CheckpointMonitor(interval=0.15)
                if action == "cleanup-stop":
                    timeout = 0.5
                elif action == "observer-overflow":
                    timeout = 6
                text_observer = runner.stream_observer("Claude", monitor) if monitor else observe
                if observer:
                    text_observer = overflow_observer
                runner._run_streaming(
                    [sys.executable, str(root / "bin" / "fixture")], dict(os.environ),
                    timeout, stdin_text="offline fixture", stream_journal=root / "journal.jsonl",
                    text_observer=text_observer, semantic_monitor=monitor,
                    capture_output=action != "observer-overflow",
                )
                stats["returned"] = True
    except BaseException as error:
        stats["exception"] = type(error).__name__
        stats["exception_text"] = str(error)[:500]
        if isinstance(error, SystemExit):
            stats["system_exit"] = error.code
        stdout, stderr = getattr(error, "output", None), getattr(error, "stderr", None)
        stats["stdout_bytes"] = len(stdout.encode("utf-8")) if isinstance(stdout, str) else 0
        stats["stderr_bytes"] = len(stderr.encode("utf-8")) if isinstance(stderr, str) else 0
        stats["stdout_tail"] = isinstance(stdout, str) and TAIL in stdout
        stats["stderr_tail"] = isinstance(stderr, str) and TAIL + "_STDERR" in stderr
    stats["elapsed"] = time.monotonic() - started
    stats["shutdown_latched"] = runner.shutdown_requested()
    stats["latched_signal"] = runner._SHUTDOWN["signal"]
    stats["provider_returncodes"] = [proc.poll() for proc in owned_processes]
    if monitor:
        stats["semantic_misses"] = monitor.misses
        stats["semantic_failed"] = monitor.failed
    stats["live_reader_threads"] = [thread.name for thread in threading.enumerate()
                                    if thread is not threading.main_thread()]
    (root / "result.json").write_text(json.dumps(stats), encoding="utf-8")
    if main_stop:
        return stats.get("main_return", stats.get("system_exit", 1))
    return 0


@unittest.skipUnless(os.name == "posix", "process-group regressions require POSIX")
class LifecycleTests(unittest.TestCase):
    def test_healthy_timeout_tail_preserves_queued_session_and_checkpoint(self):
        outcome = self.drive("healthy-tail", "healthy-tail")
        stats = outcome["stats"]
        self.assertEqual(stats.get("exception"), "TimeoutExpired")
        self.assertTrue(stats["queued_session"])
        self.assertTrue(stats["queued_checkpoint"])
        self.assertEqual(stats["tail_session"], {"id": "11111111-1111-4111-8111-111111111111"})
        self.assertEqual(len(stats["tail_checkpoints"]), 1)
        self.assertIn("verified codex tranche 1", stats["tail_checkpoints"][0]["new_results"])
        journal = outcome["journals"]["journal.jsonl"]["text"]
        self.assertIn("thread.started", journal)
        self.assertIn("END CHECKPOINT", journal)

    def test_healthy_timeout_tail_enables_targeted_same_session_recovery(self):
        outcome = self.drive("main-tail", "healthy-tail")
        self.assertNotIn("exception", outcome["stats"])
        self.assertIn("CONSENSUS REACHED", outcome["debate.md"])
        codex_calls = [call for call in outcome["calls"] if call["provider"] == "codex"]
        self.assertEqual(len(codex_calls), 2)
        self.assertFalse(codex_calls[0]["resumed"])
        self.assertTrue(codex_calls[1]["resumed"])
        self.assertTrue(codex_calls[1]["recovery"])
        self.assertEqual(codex_calls[0]["session"], codex_calls[1]["session"])
        self.assertIn("verified codex tranche 1", outcome["progress.md"])
        self.assertIn("recovery: yes", outcome["progress.md"])

    def test_termination_tail_checkpoint_cannot_undo_two_miss_stop(self):
        outcome = self.drive("semantic-stop", "semantic-tail")
        stats = outcome["stats"]
        self.assertEqual(stats.get("exception"), "RuntimeError")
        self.assertIn("two consecutive missed", stats["exception_text"])
        self.assertEqual(stats["semantic_misses"], 2)
        self.assertTrue(stats["semantic_failed"])
        self.assertIn("CHECKPOINT", outcome["journals"]["journal.jsonl"]["text"])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="consensus-lifecycle-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sequence = 0

    def drive(self, action, mode, *, fail_start=0, resume=False, signals=False,
              boundary_signal=False, cleanup_signal=False, expected_exit=0):
        self.sequence += 1
        root = self.root / str(self.sequence)
        (root / "bin").mkdir(parents=True)
        (root / "tmp").mkdir()
        for name in ("claude", "codex", "fixture"):
            executable = root / "bin" / name
            executable.write_text("#!" + sys.executable + "\n" + FAKE_CLI,
                                  encoding="utf-8")
            executable.chmod(0o700)
        environment = dict(os.environ)
        for key in tuple(environment):
            if key.startswith("CONSENSUS_") or key in {
                "CLAUDE_CODE_DISABLE_THINKING", "MAX_THINKING_TOKENS",
            }:
                environment.pop(key)
        environment.update({
            "LIFECYCLE_CASE": str(root), "LIFECYCLE_MODE": mode,
            "LIFECYCLE_FAIL_START": str(fail_start), "LIFECYCLE_RESUME": str(int(resume)),
            "PATH": str(root / "bin"), "TMPDIR": str(root / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        proc = None
        fired = False
        pending_error = None
        outcome = {}
        deadline = time.monotonic() + WATCHDOG
        try:
            with (root / "worker.stdout").open("w") as out, \
                    (root / "worker.stderr").open("w") as err:
                proc = subprocess.Popen(
                    [sys.executable, "-B", str(SELF), "--worker", action], env=environment,
                    cwd=root, stdout=out, stderr=err, start_new_session=True,
                )
                if boundary_signal:
                    self.assertTrue(wait_for_file(root / "signal-ready", proc, deadline),
                                    "worker never reached the requested signal boundary")
                    proc.send_signal(signal.SIGTERM)
                elif cleanup_signal:
                    self.assertTrue(wait_for_file(root / "term-received", proc, deadline),
                                    "ordinary cleanup never delivered TERM to the owned fixture")
                    # Distinguish original-TERM+2 from signal-arrival+2 as well
                    # as the old five-second grace, with a generous timing band.
                    time.sleep(1)
                    proc.send_signal(signal.SIGTERM)
                if signals:
                    self.assertTrue(wait_for_file(root / "fixture-ready", proc, deadline),
                                    "fixture never became ready for cancellation")
                    proc.send_signal(signal.SIGTERM)
                    self.assertTrue(wait_for_file(root / "term-received", proc, deadline),
                                    "fixture never observed runner cleanup SIGTERM")
                    if action == "repeated-signal":
                        proc.send_signal(signal.SIGTERM)
                        time.sleep(0.05)
                        proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=max(0.01, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    fired = True
                ledger = read_records(root / "owned-pids.jsonl")
                outcome["owned_pids"] = [entry["pid"] for entry in ledger]
                outcome["live_pids_before_cleanup"] = [pid for pid in outcome["owned_pids"]
                                                       if pid_alive(pid)]
                outcome["roles"] = [entry["role"] for entry in ledger]
                outcome["returncode"] = proc.poll()
                outcome["watchdog_fired"] = fired
                outcome["stats"] = json.loads((root / "result.json").read_text()) \
                    if (root / "result.json").exists() else {}
                outcome["calls"] = read_records(root / "calls.jsonl")
                outcome["pipes_closed"] = (root / "pipes-closed").exists()
                outcome["term_received"] = (root / "term-received").exists()
                outcome["fifo_created"] = (root / "fifo-created").exists()
                outcome["writer_completed"] = (root / "writer-completed").exists()
        except BaseException as error:
            pending_error = error
        finally:
            # Cleanup is a safety net, not acceptance evidence. Tests assert the
            # liveness snapshot above, taken before any watchdog intervention.
            if proc is not None:
                for entry in reversed(read_records(root / "owned-pids.jsonl")):
                    kill_owned_pid(entry["pid"])
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        if pending_error is not None:
            raise pending_error
        outcome["stdout"] = (root / "worker.stdout").read_text(encoding="utf-8")
        outcome["stderr"] = (root / "worker.stderr").read_text(encoding="utf-8")
        for name in ("debate.md", "progress.md"):
            path = root / name
            outcome[name] = path.read_text(encoding="utf-8") if path.exists() else ""
        outcome["journals"] = {}
        for path in (root / "journal.jsonl", root / "progress.md.claude-stream.jsonl",
                     root / "progress.md.codex-stream.jsonl"):
            if path.exists():
                rows = read_records(path)
                outcome["journals"][path.name] = {
                    "text": "".join(row.get("line", "") for row in rows),
                    "raw": {
                        stream: b"".join(base64.b64decode(row["raw_b64"], validate=True)
                                         for row in rows if row.get("stream") == stream)
                        for stream in ("stdout", "stderr")
                    },
                    "mode": stat.S_IMODE(path.stat().st_mode),
                }
        self.assertFalse(fired, f"outer watchdog fired: {action}; {outcome['stderr'][-1000:]}")
        self.assertEqual(outcome["returncode"], expected_exit,
                         {"stats": outcome["stats"], "stderr": outcome["stderr"][-1000:]})
        self.assertTrue(outcome["owned_pids"], "no fake process was actually started")
        self.assertEqual(outcome["live_pids_before_cleanup"], [],
                         "runner leaked owned processes before watchdog cleanup")
        self.assertFalse(outcome["stats"].get("live_reader_threads"),
                         "runner left output reader or stdin feeder threads alive: "
                         + str(outcome["stats"]))
        for journal in outcome["journals"].values():
            self.assertEqual(journal["mode"], 0o600)
        return outcome

    def test_codex_fifo_output_is_rejected_without_blocking(self):
        for resumed in (False, True):
            with self.subTest(resumed=resumed):
                outcome = self.drive("codex-fifo", "fifo", resume=resumed)
                self.assertTrue(outcome["fifo_created"])
                self.assertEqual(outcome["stats"].get("exception"), "RuntimeError")
                self.assertIn("not a regular file", outcome["stats"]["exception_text"])
                self.assertLess(outcome["stats"]["elapsed"], 4)
                self.assertEqual(outcome["stats"]["provider_returncodes"], [0])
                self.assertEqual(len(outcome["calls"]), 1)
                self.assertEqual(outcome["calls"][0]["resumed"], resumed)

    def test_codex_fifo_output_with_late_sigterm_does_not_hang(self):
        outcome = self.drive("codex-fifo-stop", "fifo", boundary_signal=True)
        self.assertTrue(outcome["fifo_created"])
        self.assertEqual(outcome["stats"].get("exception"), "ShutdownRequested")
        self.assertEqual(outcome["stats"]["latched_signal"], signal.SIGTERM)
        self.assertEqual(outcome["stats"]["boundary_provider_returncode"], 0)
        self.assertEqual(len(outcome["calls"]), 1)
        self.assertLess(outcome["stats"]["elapsed"], 4)

    def test_completed_burst_is_fully_journaled_after_observer_overflow(self):
        outcome = self.drive("observer-overflow", "observer-burst")
        stats = outcome["stats"]
        self.assertTrue(outcome["writer_completed"])
        self.assertTrue(stats["burst_retained_before_failure"])
        self.assertEqual(stats.get("exception"), "StreamLimitExceeded")
        self.assertIn("individual JSON event", stats["exception_text"])
        self.assertTrue(stats["observer_failed"])
        self.assertEqual(stats.get("observer_calls_after_failure", 0), 0)
        self.assertEqual(stats["provider_returncodes"], [0])
        raw = outcome["journals"]["journal.jsonl"]["raw"]
        self.assertEqual(raw["stdout"], BURST_STDOUT)
        self.assertEqual(raw["stderr"], BURST_STDERR)
        self.assertLess(len(BURST_STDOUT) + len(BURST_STDERR), stats["raw_limit"])
        self.assertLessEqual(stats["queue_peak"], 64)
        self.assertLess(stats["elapsed"], 5)

    def assert_interrupted_main(self, mode, calls):
        outcome = self.drive("main-stop", mode, boundary_signal=True, expected_exit=130)
        stats = outcome["stats"]
        self.assertNotIn("exception", stats)
        self.assertEqual(stats["main_return"], 130)
        self.assertTrue(stats["shutdown_latched"])
        self.assertEqual(stats["latched_signal"], signal.SIGTERM)
        self.assertEqual(len(outcome["calls"]), calls, "stop must not spawn recovery or synthesis")
        self.assertEqual(stats["provider_returncodes"], [0] * calls)
        transcript = outcome["debate.md"]
        self.assertIn("## Outcome\n", transcript)
        terminal = transcript.split("## Outcome\n", 1)[1].splitlines()[0]
        self.assertIn("INTERRUPTED", terminal)
        self.assertNotIn("CONSENSUS", terminal)
        self.assertNotIn("ABORTED", transcript)
        progress = outcome["progress.md"]
        self.assertIn("Run interrupted", progress)
        self.assertEqual(progress.count("Run interrupted"), 1)
        self.assertNotIn("Run completed", progress)
        self.assertNotIn("Run aborted", progress)
        self.assertNotIn("invalid report", progress)
        self.assertNotIn("recovery: yes", progress)
        return outcome

    def test_sigterm_during_last_agreement_cleanup_is_interrupted_without_synthesis(self):
        outcome = self.assert_interrupted_main("final-agree", 2)
        self.assertEqual(outcome["stats"]["successful_cleanups"], 2)
        self.assertEqual(outcome["stats"]["boundary_provider_returncode"], 0)

    def test_sigterm_during_final_disagree_cleanup_is_interrupted(self):
        outcome = self.assert_interrupted_main("final-disagree", 2)
        self.assertEqual(outcome["stats"]["successful_cleanups"], 2)
        self.assertIn("VERDICT: DISAGREE", outcome["journals"]["progress.md.codex-stream.jsonl"]["text"])

    def test_sigterm_during_successful_synthesis_cleanup_is_interrupted(self):
        outcome = self.assert_interrupted_main("synthesis-cleanup", 3)
        self.assertEqual(outcome["stats"]["successful_cleanups"], 3)
        self.assertEqual(outcome["stats"]["boundary_provider_returncode"], 0)
        self.assertIn("Claude synthesis session started", outcome["progress.md"])
        # The earlier research agreement is legitimately already on stdout.
        self.assertIn("CONSENSUS REACHED", outcome["stdout"])

    def test_active_cancellation_and_pending_synthesis_controls_stay_interrupted(self):
        for mode in ("live-control", "pending-synthesis"):
            with self.subTest(mode=mode):
                self.assert_interrupted_main(mode, 2)

    def test_sigterm_takes_priority_over_injected_research_oserror(self):
        outcome = self.assert_interrupted_main("research-oserror", 2)
        self.assertTrue(outcome["stats"]["injected_research_oserror"])

    def test_stop_during_cleanup_shortens_grace_from_original_term_time(self):
        outcome = self.drive("cleanup-stop", "resist", cleanup_signal=True)
        stats = outcome["stats"]
        self.assertEqual(stats.get("exception"), "ShutdownRequested")
        self.assertTrue(stats["shutdown_latched"])
        self.assertEqual(stats["latched_signal"], signal.SIGTERM)
        signals = stats["group_signals"]
        terms = [entry for entry in signals if entry["signal"] == signal.SIGTERM]
        kills = [entry for entry in signals if entry["signal"] == signal.SIGKILL]
        self.assertEqual(len(terms), 1)
        self.assertEqual(len(kills), 1)
        self.assertFalse(terms[0]["stopping"], "TERM must precede the external stop")
        self.assertTrue(kills[0]["stopping"])
        self.assertEqual(terms[0]["pgid"], kills[0]["pgid"])
        self.assertIn(terms[0]["pgid"], outcome["owned_pids"])
        term_to_kill = kills[0]["time"] - terms[0]["time"]
        self.assertGreaterEqual(term_to_kill, 1.8)
        self.assertLess(term_to_kill, 2.75, "cleanup kept or restarted its old TERM grace")
        self.assertEqual(stats["provider_returncodes"], [-signal.SIGKILL])

    def assert_capped_eof(self, provider):
        for resumed in (False, True):
            with self.subTest(provider=provider, resumed=resumed):
                outcome = self.drive(provider + "-eof", "eof", resume=resumed)
                self.assertTrue(outcome["pipes_closed"], {"stats": outcome["stats"], "calls": outcome["calls"], "stderr": outcome["stderr"]})
                self.assertEqual(outcome["stats"].get("exception"), "TimeoutExpired")
                self.assertLess(outcome["stats"]["elapsed"], 4)
                self.assertEqual(len(outcome["calls"]), 1)
                self.assertEqual(outcome["calls"][0]["resumed"], resumed)

    def test_claude_eof_before_exit_honors_fresh_and_resumed_caps(self):
        self.assert_capped_eof("claude")

    def test_codex_eof_before_exit_honors_fresh_and_resumed_caps(self):
        self.assert_capped_eof("codex")

    def assert_start_failure(self, index):
        outcome = self.drive("startup", "startup", fail_start=index)
        self.assertEqual(outcome["stats"].get("exception"), "RuntimeError")
        self.assertEqual(outcome["stats"].get("injected_thread_start"), index)
        self.assertIn("injected Thread.start failure", outcome["stats"]["exception_text"])
        self.assertIn("descendant", outcome["roles"])
        self.assertTrue(outcome["term_received"])
        self.assertLess(outcome["stats"]["elapsed"], 5)

    def test_first_reader_thread_start_failure_cleans_group(self):
        self.assert_start_failure(1)

    def test_second_reader_thread_start_failure_cleans_group(self):
        self.assert_start_failure(2)

    def test_stdin_feeder_thread_start_failure_cleans_group(self):
        self.assert_start_failure(3)

    def test_repeated_sigterm_does_not_interrupt_cleanup_escalation(self):
        outcome = self.drive("repeated-signal", "resist", signals=True)
        self.assertEqual(outcome["stats"].get("exception"), "ShutdownRequested")
        self.assertEqual(outcome["stats"]["interrupts"], 1)
        self.assertTrue(outcome["stats"]["shutdown_latched"])
        self.assertEqual(outcome["stats"]["latched_signal"], signal.SIGTERM)
        self.assertTrue(outcome["term_received"])
        self.assertLess(outcome["stats"]["elapsed"], 12)

    def test_sigterm_tail_is_persisted_after_cancellation(self):
        outcome = self.drive("interrupt-tail", "tail", signals=True)
        self.assertEqual(outcome["stats"].get("exception"), "KeyboardInterrupt")
        self.assertTrue(outcome["stats"]["tail_read"])
        journal = outcome["journals"]["journal.jsonl"]["text"]
        self.assertIn(TAIL, journal)
        self.assertIn(TAIL + "_STDERR", journal)

    def test_timeout_tail_is_journaled_and_attached_to_timeout_output(self):
        outcome = self.drive("timeout-tail", "tail")
        self.assertEqual(outcome["stats"].get("exception"), "TimeoutExpired")
        self.assertTrue(outcome["stats"]["tail_read"])
        self.assertTrue(outcome["stats"]["stdout_tail"])
        self.assertTrue(outcome["stats"]["stderr_tail"])
        journal = outcome["journals"]["journal.jsonl"]["text"]
        self.assertIn(TAIL, journal)
        self.assertIn(TAIL + "_STDERR", journal)
        self.assertLess(outcome["stats"]["elapsed"], 4)

    def assert_resource_limit(self, action, mode, message):
        outcome = self.drive(action, mode)
        stats = outcome["stats"]
        self.assertEqual(stats.get("exception"), "StreamLimitExceeded")
        self.assertIn(message, stats["exception_text"])
        self.assertEqual(stats["default_capture_limit"], 2_000_000)
        self.assertEqual(stats["default_raw_limit"], 64_000_000)
        self.assertEqual(stats["queue_capacity"], 64)
        self.assertLessEqual(stats["queue_peak"], 64)
        self.assertGreater(stats["max_chunk_bytes"], 0)
        self.assertLessEqual(stats["max_chunk_bytes"], 65536)
        self.assertLessEqual(stats["observer_bytes"], stats["capture_limit"])
        journal_bytes = len(outcome["journals"]["journal.jsonl"]["text"].encode("utf-8"))
        self.assertLessEqual(journal_bytes, stats["raw_limit"])
        self.assertLess(stats["elapsed"], 3)

    def test_raw_stream_limit_stops_unterminated_line_producer(self):
        self.assert_resource_limit("raw-limit", "raw-flood", "raw stream")

    def test_retained_stream_limit_stops_unterminated_line_producer(self):
        self.assert_resource_limit("capture-limit", "capture-flood", "captured stream")

    def test_retained_limit_counts_stdout_and_stderr_together(self):
        self.assert_resource_limit("capture-limit", "mixed-flood", "captured stream")

    def assert_stable_sessions(self, mode):
        outcome = self.drive("main", mode)
        self.assertNotIn("exception", outcome["stats"], {"stats": outcome["stats"], "stdout": outcome["stdout"][-4000:], "stderr": outcome["stderr"], "calls": outcome["calls"]})
        self.assertIn("CONSENSUS REACHED", outcome["stdout"])
        self.assertEqual(len(outcome["calls"]), 4)
        for provider in ("claude", "codex"):
            calls = [call for call in outcome["calls"] if call["provider"] == provider]
            self.assertEqual(len(calls), 2)
            self.assertFalse(calls[0]["resumed"])
            self.assertTrue(calls[1]["resumed"])
            self.assertEqual(calls[0]["session"], calls[1]["session"])
            self.assertFalse(calls[0]["recovery"])
            self.assertEqual(calls[1]["recovery"], mode != "tranches")
            if provider == "claude" and mode != "tranches":
                self.assertTrue(calls[1]["thinking_disabled"])
            journal = outcome["journals"]["progress.md." + provider + "-stream.jsonl"]
            self.assertIn("CHECKPOINT", journal["text"])
        self.assertIn("after 4 turns" if mode == "tranches" else "after 2 turns",
                      outcome["stdout"])

    def test_both_providers_resume_stable_sessions_across_normal_tranches(self):
        self.assert_stable_sessions("tranches")

    def test_both_providers_recover_invalid_reports_in_same_sessions(self):
        self.assert_stable_sessions("invalid-recovery")

    def test_both_providers_resume_same_sessions_after_eof_timeout(self):
        self.assert_stable_sessions("cap-recovery")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        raise SystemExit(worker(sys.argv[2]))
    else:
        unittest.main()
