#!/usr/bin/env python3
"""Offline hardening regressions against the adjacent live consensus runner.

Run with ``python3 -B scripts/test_hardening.py -v``. Missing runtime helpers
are failures, not skips. All fixtures live in system temporary directories;
provider subprocess creation and documentation fetching are blocked.

Validation helpers are expected to raise ValueError on invalid input (or return
None/False for report validation). CheckpointMonitor uses observe(checkpoint)
and tick(), with time.monotonic patched so no deadline test needs to sleep.
"""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock


RUNNER = Path(__file__).with_name("consensus")
FIELDS = (
    "elapsed", "tentative_verdict", "new_results", "current_obstruction",
    "next_bounded_step", "token_cost",
)
ANSWER = "The checked counterexample refutes the universal claim."
REPORT_BYTE_LIMIT = 40000


def checkpoint(new_results="NEW: verified a counterexample", **changes):
    values = dict(zip(FIELDS, (
        "1 second", "AGREE", new_results, "none", "done", "unavailable",
    )))
    values.update(changes)
    return "\n".join(
        ["CHECKPOINT"]
        + [f"{key}: {value}" for key, value in values.items()]
        + ["END CHECKPOINT"]
    )


def report(body=ANSWER, *, verdict="AGREE", new_results="NEW: checked fact"):
    return f"{checkpoint(new_results)}\n\n{body}\nVERDICT: {verdict}"


def metadata(value="gpt-6.1-sol"):
    return f"---\nlatestModelInfo:\n  model: {value}\n---\nGuide body.\n"


def assistant_event(text):
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    }


def terminal_event(**changes):
    event = {"type": "result", "subtype": "success", "is_error": False}
    event.update(changes)
    return event


class RunnerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Compile in memory rather than importing the extensionless runner in a
        # way that can leave bytecode or call its CLI entry point.
        cls.runner = types.ModuleType("consensus_hardening_runtime")
        cls.runner.__file__ = str(RUNNER)
        sys.modules[cls.runner.__name__] = cls.runner
        with ExitStack() as stack:
            for target in (
                "subprocess.Popen", "subprocess.run", "urllib.request.urlopen",
            ):
                stack.enter_context(mock.patch(
                    target, side_effect=AssertionError("offline test attempted I/O"),
                ))
            exec(compile(RUNNER.read_bytes(), str(RUNNER), "exec"),
                 cls.runner.__dict__)

    def setUp(self):
        self.guards = ExitStack()
        self.addCleanup(self.guards.close)
        for target in (
            "subprocess.Popen", "subprocess.run", "urllib.request.urlopen",
        ):
            self.guards.enter_context(mock.patch(
                target, side_effect=AssertionError("offline test attempted I/O"),
            ))
        if hasattr(self.runner, "urlopen"):
            self.guards.enter_context(mock.patch.object(
                self.runner, "urlopen",
                side_effect=AssertionError("offline test attempted network access"),
            ))
        temporary = tempfile.TemporaryDirectory(prefix="consensus-hardening-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def helper(self, name):
        function = getattr(self.runner, name, None)
        self.assertTrue(callable(function), f"required runtime helper missing: {name}")
        return function

    def assert_report_valid(self, text, *, synthesis=False):
        result = self.helper("validate_report")(text, synthesis=synthesis)
        self.assertIsNotNone(result, "valid completed report was rejected")
        self.assertIsNot(result, False, "valid completed report was rejected")
        return result

    def assert_report_invalid(self, text, *, synthesis=False):
        try:
            result = self.helper("validate_report")(text, synthesis=synthesis)
        except ValueError:
            return
        self.assertTrue(result is None or result is False,
                        f"invalid completed report was accepted: {result!r}")


class AtomicWriteTests(RunnerCase):
    def test_fixed_tmp_symlink_does_not_overwrite_victim(self):
        victim = self.root / "private-input.txt"
        victim.write_text("original private input", encoding="utf-8")
        destination = self.root / "report.md"
        destination.write_text("old report", encoding="utf-8")
        unsafe_tmp = Path(str(destination) + ".tmp")
        unsafe_tmp.symlink_to(victim)
        try:
            self.helper("atomic_write")(destination, "new report")
        except (OSError, ValueError):
            self.assertEqual(destination.read_text(), "old report")
        else:
            self.assertFalse(destination.is_symlink())
            self.assertEqual(destination.read_text(), "new report")
        self.assertEqual(victim.read_text(), "original private input")
        self.assertTrue(unsafe_tmp.is_symlink(), "unowned temporary was consumed")
        self.assertEqual(Path(os.readlink(unsafe_tmp)), victim)

    def test_dangling_fixed_tmp_symlink_does_not_create_victim(self):
        destination = self.root / "report.md"
        victim = self.root / "must-not-be-created.txt"
        unsafe_tmp = Path(str(destination) + ".tmp")
        unsafe_tmp.symlink_to(victim)
        try:
            self.helper("atomic_write")(destination, "safe report")
        except (OSError, ValueError):
            self.assertFalse(destination.exists())
        else:
            self.assertFalse(destination.is_symlink())
            self.assertEqual(destination.read_text(), "safe report")
        self.assertFalse(victim.exists())
        self.assertTrue(unsafe_tmp.is_symlink())

    def test_fixed_tmp_regular_file_is_not_truncated_or_consumed(self):
        destination = self.root / "report.md"
        unowned = Path(str(destination) + ".tmp")
        unowned.write_text("another writer owns this", encoding="utf-8")
        try:
            self.helper("atomic_write")(destination, "safe report")
        except (OSError, ValueError):
            self.assertFalse(destination.exists())
        else:
            self.assertEqual(destination.read_text(), "safe report")
        self.assertEqual(unowned.read_text(), "another writer owns this")

    def test_replacement_preserves_0600_under_umask_022(self):
        destination = self.root / "private-report.md"
        destination.write_text("old", encoding="utf-8")
        destination.chmod(0o600)
        previous = os.umask(0o022)
        try:
            self.helper("atomic_write")(destination, "replacement")
        finally:
            os.umask(previous)
        self.assertEqual(destination.read_text(), "replacement")
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_explicit_private_creation_under_umask_000(self):
        destination = self.root / "new-private-report.md"
        previous = os.umask(0)
        try:
            self.helper("atomic_write")(destination, "private", mode=0o600)
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_destination_symlink_is_rejected_without_mutating_target(self):
        victim = self.root / "private-input.txt"
        victim.write_text("preserve", encoding="utf-8")
        destination = self.root / "report.md"
        destination.symlink_to(victim)
        with self.assertRaises((OSError, ValueError)):
            self.helper("atomic_write")(destination, "replacement")
        self.assertTrue(destination.is_symlink())
        self.assertEqual(victim.read_text(), "preserve")

    def test_concurrent_writers_succeed_with_distinct_atomic_temporaries(self):
        destination = self.root / "shared-report.md"
        destination.write_text("initial report", encoding="utf-8")
        destination.chmod(0o600)
        payloads = [f"writer={index}\n" + chr(65 + index) * 131072 + "\nEND\n"
                    for index in range(4)]
        barrier = threading.Barrier(len(payloads))
        lock = threading.Lock()
        temporaries = []
        replacements = []
        real_mkstemp = self.runner.tempfile.mkstemp
        real_replace = self.runner.os.replace
        write = self.helper("atomic_write")

        def recorded_mkstemp(*args, **kwargs):
            descriptor, path = real_mkstemp(*args, **kwargs)
            with lock:
                temporaries.append(Path(path))
            return descriptor, path

        def synchronized_replace(source, target):
            with lock:
                replacements.append((Path(source), Path(target)))
            # All writers finish their own temporary before any can replace the
            # shared destination. A fixed temporary cannot survive this race.
            barrier.wait(timeout=10)
            return real_replace(source, target)

        def write_and_read(payload):
            write(destination, payload)
            return destination.read_text(encoding="utf-8")

        with mock.patch.object(self.runner.tempfile, "mkstemp", recorded_mkstemp), \
                mock.patch.object(self.runner.os, "replace", synchronized_replace), \
                ThreadPoolExecutor(max_workers=len(payloads)) as executor:
            futures = [executor.submit(write_and_read, payload) for payload in payloads]
            observations = [future.result(timeout=15) for future in futures]

        self.assertEqual(len(temporaries), len(payloads))
        self.assertEqual(len(set(temporaries)), len(payloads))
        self.assertEqual({source for source, _ in replacements}, set(temporaries))
        self.assertEqual({target for _, target in replacements}, {destination})
        for temporary in temporaries:
            self.assertEqual(temporary.parent, self.root)
            self.assertFalse(temporary.exists(), "successful replacement left its temporary")
        for observed in observations:
            self.assertIn(observed, payloads, "reader observed a torn or mixed report")
        self.assertIn(destination.read_text(encoding="utf-8"), payloads)
        self.assertFalse(destination.is_symlink())
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertEqual(list(self.root.iterdir()), [destination])


class ArtifactPathTests(RunnerCase):
    def paths(self):
        return tuple(self.root / name for name in (
            "debate.md", "debate.progress.md", "debate.stream.jsonl",
            "debate.protocol.json",
        ))

    def test_disjoint_new_paths_are_accepted_without_creating_files(self):
        self.helper("validate_artifact_paths")(self.paths())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_every_artifact_pair_rejects_identical_paths(self):
        validate = self.helper("validate_artifact_paths")
        for left in range(4):
            for right in range(left + 1, 4):
                with self.subTest(left=left, right=right):
                    paths = list(self.paths())
                    paths[right] = paths[left]
                    with self.assertRaises(ValueError):
                        validate(paths)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_canonical_dotdot_alias_is_rejected(self):
        (self.root / "subdirectory").mkdir()
        path = self.root / "debate.md"
        alias = self.root / "subdirectory" / ".." / "debate.md"
        with self.assertRaises(ValueError):
            self.helper("validate_artifact_paths")((path, alias))
        self.assertFalse(path.exists())

    def test_existing_file_requires_explicit_overwrite(self):
        existing = self.paths()[0]
        existing.write_text("previous transcript", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.helper("validate_artifact_paths")(self.paths())
        self.assertEqual(existing.read_text(), "previous transcript")
        self.assertEqual(list(self.root.iterdir()), [existing])

    def test_explicit_overwrite_allows_regular_file_without_mutating_it(self):
        existing = self.paths()[0]
        existing.write_text("previous transcript", encoding="utf-8")
        self.helper("validate_artifact_paths")(self.paths(), overwrite=True)
        self.assertEqual(existing.read_text(), "previous transcript")
        self.assertEqual(list(self.root.iterdir()), [existing])

    def test_hardlink_collision_rejected_even_with_overwrite(self):
        first = self.root / "first.md"
        second = self.root / "second.md"
        first.write_text("preserve", encoding="utf-8")
        os.link(first, second)
        with self.assertRaises(ValueError):
            self.helper("validate_artifact_paths")((first, second), overwrite=True)
        self.assertEqual(first.read_text(), "preserve")
        self.assertEqual(second.read_text(), "preserve")

    def test_symlink_output_rejected_even_with_overwrite(self):
        victim = self.root / "victim.txt"
        victim.write_text("preserve", encoding="utf-8")
        output = self.root / "report.md"
        output.symlink_to(victim)
        with self.assertRaises(ValueError):
            self.helper("validate_artifact_paths")((output,), overwrite=True)
        self.assertTrue(output.is_symlink())
        self.assertEqual(victim.read_text(), "preserve")

    def test_dangling_symlink_output_rejected_without_mutation(self):
        output = self.root / "report.md"
        victim = self.root / "missing.txt"
        output.symlink_to(victim)
        with self.assertRaises(ValueError):
            self.helper("validate_artifact_paths")((output,))
        self.assertTrue(output.is_symlink())
        self.assertFalse(victim.exists())

    def test_input_output_collision_is_never_authorized_by_overwrite(self):
        source = self.root / "question.txt"
        source.write_text("private question", encoding="utf-8")
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                with self.assertRaises(ValueError):
                    self.helper("validate_artifact_paths")(
                        (source,), input_paths=(source,), overwrite=overwrite,
                    )
        self.assertEqual(source.read_text(), "private question")

    def test_input_output_hardlink_collision_is_rejected(self):
        source = self.root / "question.txt"
        source.write_text("private question", encoding="utf-8")
        output = self.root / "report.md"
        os.link(source, output)
        with self.assertRaises(ValueError):
            self.helper("validate_artifact_paths")(
                (output,), input_paths=(source,), overwrite=True,
            )
        self.assertEqual(source.read_text(), "private question")

    def test_string_input_output_identity_is_rejected(self):
        source = self.root / "question.txt"
        source.write_text("private question", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.helper("validate_artifact_paths")(
                (str(source),), input_paths=(str(source),), overwrite=True,
            )
        self.assertEqual(source.read_text(), "private question")

    def test_directory_output_is_rejected(self):
        directory = self.root / "directory"
        directory.mkdir()
        with self.assertRaises(ValueError):
            self.helper("validate_artifact_paths")((directory,), overwrite=True)
        self.assertTrue(directory.is_dir())


class StructuredMetadataTests(RunnerCase):
    def test_direct_scalar_model_variants(self):
        parser = self.helper("parse_openai_model")
        for value in ("gpt-6.1-sol", "'gpt-6.1-sol'", '"gpt-6.1-sol"'):
            with self.subTest(value=value):
                self.assertEqual(parser(metadata(value)), "gpt-6.1-sol")

    def test_body_examples_do_not_override_direct_metadata(self):
        document = metadata() + (
            "```yaml\nlatestModelInfo:\n  model: gpt-old-example\n```\n"
        )
        self.assertEqual(self.helper("parse_openai_model")(document), "gpt-6.1-sol")

    def test_nested_example_is_not_selected_over_direct_model(self):
        document = (
            "---\nlatestModelInfo:\n  example:\n    model: gpt-old-example\n"
            "  model: gpt-6.1-sol\n---\n"
        )
        try:
            model = self.helper("parse_openai_model")(document)
        except ValueError:
            return  # Rejecting unsupported nested structure is also fail-closed.
        self.assertEqual(model, "gpt-6.1-sol")

    def test_rejects_ambiguous_or_nonstructured_metadata(self):
        cases = {
            "duplicate model key":
                "---\nlatestModelInfo:\n  model: gpt-old\n  model: gpt-new\n---\n",
            "duplicate identical key":
                "---\nlatestModelInfo:\n  model: gpt-new\n  model: gpt-new\n---\n",
            "duplicate metadata block":
                "---\nlatestModelInfo:\n  model: gpt-old\n"
                "latestModelInfo:\n  model: gpt-new\n---\n",
            "nested model only":
                "---\nlatestModelInfo:\n  example:\n    model: gpt-example\n---\n",
            "body field only": "latestModelInfo:\n  model: gpt-example\n",
            "fenced example only":
                "# Guide\n```yaml\nlatestModelInfo:\n  model: gpt-example\n```\n",
            "missing model": "---\nlatestModelInfo:\n  title: New model\n---\n",
            "missing closing delimiter": "---\nlatestModelInfo:\n  model: gpt-new\n",
            "unmatched double quote": metadata('"gpt-new'),
            "unmatched single quote": metadata("'gpt-new"),
            "mismatched quotes": metadata("'gpt-new\""),
            "sequence instead of scalar": metadata("[gpt-new, gpt-old]"),
            "mapping instead of scalar": metadata("{id: gpt-new}"),
            "null instead of scalar": metadata("null"),
            "boolean instead of scalar": metadata("true"),
            "numeric instead of scalar": metadata("42"),
            "block scalar": metadata("|\n    gpt-new"),
            "wrong provider": metadata("claude-opus-5-5"),
        }
        parser = self.helper("parse_openai_model")
        for name, document in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ValueError):
                    parser(document)


class CheckpointTests(RunnerCase):
    def test_latest_complete_checkpoint_wins_in_both_orders(self):
        parse = self.helper("checkpoint_of")
        stalled = checkpoint("NONE: repeated searches")
        productive = checkpoint("NEW: proved the missing lemma")
        for first, last, expected in (
            (stalled, productive, "NEW: proved the missing lemma"),
            (productive, stalled, "NONE: repeated searches"),
        ):
            with self.subTest(expected=expected):
                result = parse(f"{first}\nIntermediate report.\n{last}\n{ANSWER}")
                self.assertIsNotNone(result)
                self.assertEqual(result["new_results"], expected)
                self.assertEqual(set(result), set(FIELDS))

    def test_invalid_latest_checkpoint_does_not_fall_back_to_earlier(self):
        malformed = checkpoint().replace("token_cost: unavailable\n", "")
        self.assertIsNone(self.helper("checkpoint_of")(
            f"{checkpoint()}\nEarlier answer.\n{malformed}\nFinal answer.",
        ))

    def test_canonical_none_states_are_no_progress(self):
        for value in ("NONE", "NONE: all searches repeated"):
            with self.subTest(value=value):
                parsed = self.helper("checkpoint_of")(checkpoint(value))
                self.assertIsNotNone(parsed)
                self.assertTrue(self.helper("no_substantive_progress")(parsed))

    def test_canonical_new_fact_is_progress(self):
        parsed = self.helper("checkpoint_of")(checkpoint("NEW: a verified new fact"))
        self.assertIsNotNone(parsed)
        self.assertFalse(self.helper("no_substantive_progress")(parsed))

    def test_unrecognized_progress_states_are_rejected(self):
        for value in (
            "No substantive new result", "None (all searches repeated)",
            "no new result", "unchanged",
            "NEW", "NEW:", "NEW:   ", "NONE:", "NONE:   ",
            "MAYBE: fact", "true", "some facts", "NONEISH", "NEWNESS: fact",
        ):
            with self.subTest(value=value):
                self.assertIsNone(self.helper("checkpoint_of")(checkpoint(value)))

    def test_each_field_is_required_and_nonempty(self):
        parse = self.helper("checkpoint_of")
        for field in FIELDS:
            for empty in (False, True):
                with self.subTest(field=field, empty=empty):
                    block = checkpoint()
                    line = next(line for line in block.splitlines()
                                if line.startswith(field + ":"))
                    replacement = field + ":   " if empty else ""
                    self.assertIsNone(parse(block.replace(line, replacement)))

    def test_duplicate_checkpoint_field_is_rejected(self):
        block = checkpoint().replace(
            "END CHECKPOINT", "new_results: NONE\nEND CHECKPOINT",
        )
        self.assertIsNone(self.helper("checkpoint_of")(block))


class ReportValidationTests(RunnerCase):
    def test_indented_body_example_cannot_override_actual_checkpoint(self):
        example = "\n".join("  " + line for line in checkpoint("NONE").splitlines())
        text = report(body="Quoted example:\n" + example, new_results="NEW: proved the result")
        parsed, verdict = self.assert_report_valid(text)
        self.assertEqual(parsed["new_results"], "NEW: proved the result")
        self.assertEqual(verdict, "AGREE")
        self.assertFalse(self.helper("no_substantive_progress")(parsed))

    def test_body_sentence_mentioning_checkpoint_is_not_a_delimiter(self):
        self.assert_report_valid(report(body="The protocol token is CHECKPOINT\nThe claim was checked."))

    def test_valid_substantive_report_with_each_verdict(self):
        for verdict in ("AGREE", "DISAGREE"):
            with self.subTest(verdict=verdict):
                self.assert_report_valid(report(verdict=verdict))

    def test_empty_and_control_only_reports_are_rejected(self):
        for text in ("", " \n", "VERDICT: AGREE", report(body=""),
                     report(body=" \n\t")):
            with self.subTest(text=text):
                self.assert_report_invalid(text)

    def test_report_body_strips_checkpoint_and_verdict(self):
        self.assertEqual(self.helper("report_body")(report()), ANSWER)

    def test_completed_report_rejects_multiple_cumulative_checkpoint_blocks(self):
        text = f"{checkpoint('NONE')}\n{checkpoint()}\n{ANSWER}\nVERDICT: AGREE"
        self.assert_report_invalid(text)

    def test_missing_completed_report_checkpoint_is_invalid_for_both_models(self):
        for model in ("Claude", "Codex"):
            with self.subTest(model=model):
                self.assert_report_invalid(f"{model}: {ANSWER}\nVERDICT: AGREE")

    def test_every_checkpoint_field_is_required_for_both_models(self):
        for model in ("Claude", "Codex"):
            for field in FIELDS:
                with self.subTest(model=model, field=field):
                    text = report(body=f"{model}: {ANSWER}")
                    line = next(line for line in text.splitlines()
                                if line.startswith(field + ":"))
                    self.assert_report_invalid(text.replace(line + "\n", ""))

    def test_unknown_progress_state_invalidates_both_models(self):
        for model in ("Claude", "Codex"):
            with self.subTest(model=model):
                self.assert_report_invalid(report(
                    body=f"{model}: {ANSWER}", new_results="Unrecognized: maybe",
                ))

    def test_final_verdict_is_required_and_invalid_values_are_rejected(self):
        for ending in ("", "VERDICT: MAYBE", "VERDICT: NOT AGREE",
                       "VERDICT: AGREE\nTrailing qualification."):
            with self.subTest(ending=ending):
                self.assert_report_invalid(f"{checkpoint()}\n{ANSWER}\n{ending}")

    def test_verdict_parser_uses_last_nonempty_line(self):
        parse = self.helper("verdict_of")
        self.assertEqual(parse("VERDICT: DISAGREE\nBody\nVERDICT: AGREE\n\n"), "AGREE")
        self.assertEqual(parse("Body\nVERDICT: DISAGREE"), "DISAGREE")
        self.assertIsNone(parse("Body\nVERDICT: AGREE\nTrailing material"))
        self.assertIsNone(parse("Body\nVERDICT: NOT AGREE"))

    def test_default_conservative_guard_accepts_exactly_40000_bytes(self):
        base = report(body="")
        text = report(body="x" * (REPORT_BYTE_LIMIT - len(base.encode("utf-8"))))
        self.assertEqual(len(text.encode("utf-8")), REPORT_BYTE_LIMIT)
        self.assert_report_valid(text)

    def test_default_conservative_guard_rejects_40001_bytes(self):
        base = report(body="")
        text = report(body="x" * (REPORT_BYTE_LIMIT + 1 - len(base.encode("utf-8"))))
        self.assertEqual(len(text.encode("utf-8")), REPORT_BYTE_LIMIT + 1)
        self.assert_report_invalid(text)

    def test_report_guard_counts_utf8_bytes_not_characters(self):
        text = report(body="\u03bb" * 21000)
        self.assertLess(len(text), REPORT_BYTE_LIMIT)
        self.assertGreater(len(text.encode("utf-8")), REPORT_BYTE_LIMIT)
        self.assert_report_invalid(text)

    def test_synthesis_accepts_checkpoint_with_substantive_answer(self):
        self.assert_report_valid(report(), synthesis=True)

    def test_synthesis_rejects_empty_or_control_only_body(self):
        for text in ("", checkpoint(), report(body=""), "VERDICT: AGREE"):
            with self.subTest(text=text):
                self.assert_report_invalid(text, synthesis=True)

    def test_synthesis_rejects_explicit_terminal_dissent(self):
        self.assert_report_invalid(report(
            body="I reject the agreed conclusion.", verdict="DISAGREE",
        ), synthesis=True)

    def test_synthesis_requires_completed_checkpoint(self):
        self.assert_report_invalid(ANSWER, synthesis=True)

    def test_synthesis_uses_same_report_byte_guard(self):
        self.assert_report_invalid(f"{checkpoint()}\n" + "x" * 40000,
                                   synthesis=True)


class TerminalStreamTests(RunnerCase):
    def test_many_image_events_do_not_consume_semantic_capture(self):
        monitor = mock.Mock()
        observer = self.helper("stream_observer")("Claude", monitor)
        image = {"type": "user", "message": {"content": [{"type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "A" * 450000}}]}}
        wire = json.dumps(image) + "\n"
        for _ in range(6):
            for offset in range(0, len(wire), 65536):
                observer("stdout", wire[offset:offset + 65536])
        self.assertGreater(len(wire) * 6, self.runner.CAPTURE_MAX_BYTES)
        self.assertEqual(observer.collected_stdout(), "")
        monitor.observe.assert_not_called()
        for event in (assistant_event(report()), terminal_event(result=report())):
            observer("stdout", json.dumps(event) + "\n")
        _, answer = self.helper("claude_answer")(observer.collected_stdout())
        self.assertEqual(answer, report())
        self.assert_report_valid(answer)

    def test_single_image_larger_than_old_capture_limit_is_supported(self):
        observer = self.helper("stream_observer")("Claude")
        wire = json.dumps({"type": "user", "content": [{"type": "image",
            "source": {"type": "base64", "data": "A" * 3100000}}]}) + "\n"
        for offset in range(0, len(wire), 65536):
            observer("stdout", wire[offset:offset + 65536])
        self.assertEqual(observer.event_bytes, 0)
        self.assertEqual(observer.fragments, [])
        self.assertEqual(observer.collected_stdout(), "")

    def test_code_execution_and_mcp_payloads_do_not_consume_codex_capture(self):
        observer = self.helper("stream_observer")("Codex", session={})
        for kind in ("command_execution", "mcp_tool_call"):
            wire = json.dumps({"type": "item.completed", "item": {
                "type": kind, "result": {"image": "A" * 1100000}}}) + "\n"
            for offset in range(0, len(wire), 65536):
                observer("stdout", wire[offset:offset + 65536])
        self.assertEqual(observer.collected_stdout(), "")

    def test_superseded_reports_do_not_accumulate_in_memory(self):
        observer = self.helper("stream_observer")("Claude")
        for index in range(60):
            observer("stdout", json.dumps(assistant_event(str(index) + "x" * 39000)) + "\n")
        self.assertLess(len(observer.collected_stdout().encode()), 40000)
        self.assertIn("59", observer.collected_stdout())

    def test_thinking_and_tool_only_messages_preserve_last_text_without_crediting_it_again(self):
        monitor = mock.Mock()
        observer = self.helper("stream_observer")("Claude", monitor)
        for event in (assistant_event(report()),
                      {"type": "assistant", "message": {"content": [
                          {"type": "thinking", "thinking": "bounded diagnostic"}]}},
                      {"type": "assistant", "message": {"content": [
                          {"type": "tool_use", "name": "Read", "input": {}}]}},
                      terminal_event()):
            observer("stdout", json.dumps(event) + "\n")
        thinking, answer = self.helper("claude_answer")(observer.collected_stdout())
        self.assertEqual(thinking, "bounded diagnostic")
        self.assertEqual(answer, report())
        monitor.observe.assert_called_once()

    def test_malformed_text_is_not_erased_by_later_valid_report(self):
        observer = self.helper("stream_observer")("Claude")
        for event in (assistant_event(42), assistant_event(report()), terminal_event(result=report())):
            observer("stdout", json.dumps(event) + "\n")
        with self.assertRaisesRegex(RuntimeError, "terminal failure"):
            self.helper("claude_answer")(observer.collected_stdout())

    def test_single_event_guard_is_independent_of_report_capture(self):
        observer = self.helper("stream_observer")("Claude")
        with mock.patch.object(self.runner, "EVENT_MAX_BYTES", 4096):
            with self.assertRaisesRegex(RuntimeError, "individual JSON event"):
                observer("stdout", json.dumps({"type": "user", "data": "A" * 8192}) + "\n")

    def test_semantic_report_capture_guard_remains_enforced(self):
        observer = self.helper("stream_observer")("Claude")
        with self.assertRaisesRegex(RuntimeError, "semantic report capture"):
            observer("stdout", json.dumps(assistant_event("x" * 2100000)) + "\n")

    def test_terminal_error_remains_sticky_after_media_and_later_success(self):
        observer = self.helper("stream_observer")("Claude")
        events = [assistant_event(report()), terminal_event(is_error=True, subtype="error_max_turns"),
                  {"type": "user", "data": "A" * 1100000}, terminal_event(result=report())]
        for event in events:
            observer("stdout", json.dumps(event) + "\n")
        with self.assertRaisesRegex(RuntimeError, "terminal failure"):
            self.helper("claude_answer")(observer.collected_stdout())

    def extract(self, events, returncode=0):
        stream = "\n".join(json.dumps(event) for event in events) + "\n"
        with mock.patch.object(self.runner, "_run_streaming",
                               return_value=(returncode, stream, "")):
            return self.helper("run_claude")("offline fixture", 0, timeout=1)

    def test_successful_terminal_event_accepts_report(self):
        thinking, answer = self.extract([assistant_event(report()), terminal_event()])
        self.assertEqual(thinking, "")
        self.assertIn(ANSWER, answer)
        self.assertEqual(self.helper("verdict_of")(answer), "AGREE")

    def assert_literal_verdict_spaces(self, *, terminal_result):
        for verdict in ("AGREE", "DISAGREE"):
            for line in (f" VERDICT: {verdict}", f"VERDICT: {verdict} ",
                         f"\tVERDICT: {verdict}\t"):
                with self.subTest(terminal_result=terminal_result, line=line):
                    prefix = report(verdict=verdict).rsplit("\n", 1)[0]
                    literal = prefix + "\n" + line
                    events = (
                        [assistant_event(report()), terminal_event(result=literal + "\r\n")]
                        if terminal_result else
                        [assistant_event(literal + "\r\n"), terminal_event()]
                    )
                    _, extracted = self.extract(events)
                    self.assertEqual(extracted.rstrip("\r\n"), literal)
                    self.assertEqual(extracted.splitlines()[-1], line)
                    self.assertIsNone(self.helper("verdict_of")(extracted))
                    self.assert_report_invalid(extracted)

    def test_assistant_fallback_preserves_literal_verdict_spaces(self):
        self.assert_literal_verdict_spaces(terminal_result=False)

    def test_terminal_result_preserves_literal_verdict_spaces(self):
        self.assert_literal_verdict_spaces(terminal_result=True)

    def test_latest_completed_assistant_message_is_selected(self):
        first = report(body="Earlier incomplete result.", new_results="NONE")
        final = report(body=ANSWER, new_results="NEW: checked final counterexample")
        _, answer = self.extract([
            assistant_event(first), assistant_event(final), terminal_event(),
        ])
        self.assertEqual(answer, final)
        self.assertEqual(self.helper("checkpoint_of")(answer)["new_results"],
                         "NEW: checked final counterexample")

    def test_completed_terminal_result_takes_precedence_over_intermediate_report(self):
        final = report()
        _, answer = self.extract([
            assistant_event(report(body="Earlier position.", verdict="DISAGREE")),
            terminal_event(result=final),
        ])
        self.assertEqual(answer, final)

    def test_missing_terminal_completion_rejects_intermediate_agreement(self):
        with self.assertRaises((RuntimeError, ValueError)):
            self.extract([assistant_event(report())])

    def test_explicit_terminal_error_rejects_prior_valid_agreement(self):
        event = terminal_event(is_error=True, subtype="error_max_turns")
        with self.assertRaises((RuntimeError, ValueError)):
            self.extract([assistant_event(report()), event])

    def test_error_subtype_rejected_even_if_is_error_false(self):
        with self.assertRaises((RuntimeError, ValueError)):
            self.extract([assistant_event(report()),
                          terminal_event(subtype="error_during_execution")])

    def test_later_success_does_not_erase_explicit_terminal_error(self):
        with self.assertRaises((RuntimeError, ValueError)):
            self.extract([
                assistant_event(report()),
                terminal_event(is_error=True, subtype="error_max_turns"),
                terminal_event(),
            ])

    def test_nonboolean_terminal_error_flag_is_rejected(self):
        for value in ("false", "true", 0, 1, None):
            with self.subTest(value=value):
                with self.assertRaises((RuntimeError, ValueError)):
                    self.extract([assistant_event(report()),
                                  terminal_event(is_error=value)])

    def test_nonzero_exit_rejects_even_success_terminal_metadata(self):
        with self.assertRaises((RuntimeError, ValueError)):
            self.extract([assistant_event(report()), terminal_event()], returncode=3)

    def test_malformed_top_level_event_is_rejected_cleanly(self):
        with self.assertRaises((RuntimeError, ValueError)):
            self.extract([[], assistant_event(report()), terminal_event()])

    def test_nonstring_assistant_text_is_rejected_cleanly(self):
        event = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": 42},
        ]}}
        with self.assertRaises((RuntimeError, ValueError)):
            self.extract([event, terminal_event()])


class CodexSessionTests(RunnerCase):
    ORIGINAL_ID = "11111111-1111-4111-8111-111111111111"
    CHANGED_ID = "22222222-2222-4222-8222-222222222222"

    def test_matching_resumed_thread_id_is_accepted_without_changing_state(self):
        session = {"id": self.ORIGINAL_ID, "started": True}
        original = dict(session)
        observer = self.helper("stream_observer")("Codex", session=session)
        event = json.dumps({"type": "thread.started", "thread_id": self.ORIGINAL_ID})
        observer("stdout", event + "\n")
        self.assertEqual(session, original)

    def test_changed_resumed_thread_id_is_rejected_before_crediting_progress(self):
        session = {"id": self.ORIGINAL_ID, "started": True}
        original = dict(session)
        monitor, checkpoint_report = mock.Mock(), mock.Mock()
        observer = self.helper("stream_observer")(
            "Codex", monitor=monitor, checkpoint_report=checkpoint_report, session=session,
        )
        event = json.dumps({"type": "thread.started", "thread_id": self.CHANGED_ID})
        observer("stdout", event[:17])
        observer("stdout", event[17:])
        self.assertEqual(session, original)
        later_report = json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": report(),
        }})
        with self.assertRaisesRegex(RuntimeError, "session"):
            observer("stdout", "\n" + later_report + "\n")
        self.assertEqual(session, original)
        monitor.observe.assert_not_called()
        checkpoint_report.assert_not_called()

    def test_run_codex_resume_rejects_changed_thread_id_with_otherwise_valid_report(self):
        session = {"id": self.ORIGINAL_ID}
        events = [
            {"type": "thread.started", "thread_id": self.CHANGED_ID},
            {"type": "item.completed", "item": {"type": "agent_message", "text": report()}},
            {"type": "turn.completed"},
        ]
        stream = "\n".join(json.dumps(event) for event in events) + "\n"

        def completed_stream(command, _environment, _timeout, **kwargs):
            output = Path(command[command.index("-o") + 1])
            output.write_text(report(), encoding="utf-8")
            kwargs["text_observer"]("stdout", stream)
            return 0, stream, ""

        with mock.patch.object(self.runner, "_run_streaming", side_effect=completed_stream) as run:
            with self.assertRaisesRegex(RuntimeError, "session"):
                self.helper("run_codex")("offline resumed fixture", session=session, timeout=1)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["codex", "exec", "resume"])
        self.assertEqual(command[-2:], [self.ORIGINAL_ID, "-"])
        self.assertEqual(session, {"id": self.ORIGINAL_ID})


class FixedDefaultsTests(RunnerCase):
    def test_defaults_match_latest_human_authorization(self):
        self.assertEqual(self.runner.DEFAULT_CLAUDE_MODEL, "claude-opus-5-5")
        self.assertEqual(getattr(self.runner, "DEFAULT_CODEX_MODEL", None), "gpt-6.1-sol")

    def test_default_resolution_is_fixed_xhigh_without_documentation_fetch(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(self.runner, "CLAUDE_MODEL", "claude-opus-5-5"), \
                mock.patch.object(self.runner, "CODEX_MODEL", "gpt-6.1-sol"), \
                mock.patch.object(self.runner, "CLAUDE_EFFORT", "xhigh"), \
                mock.patch.object(self.runner, "CODEX_REASONING_EFFORT", "xhigh"), \
                mock.patch.object(self.runner, "read_model_doc",
                                  side_effect=AssertionError("default discovery is forbidden")):
            selection = self.helper("resolve_models")()
        for provider, expected in (("claude", "claude-opus-5-5"),
                                   ("openai", "gpt-6.1-sol")):
            with self.subTest(provider=provider):
                selected = selection["models"][provider]
                self.assertEqual(selected["model"], expected)
                self.assertEqual(selected["effort"], "xhigh")
                self.assertTrue(selected.get("source"))
                source = selected["source"].lower()
                self.assertNotIn("https://", source)
                self.assertNotIn("resolved metadata", source)
                self.assertNotIn("explicit consensus model override", source)
        self.assertTrue(selection.get("resolved_at"))

    def test_default_efforts_are_xhigh_in_clean_environment(self):
        module = types.ModuleType("consensus_clean_defaults")
        module.__file__ = str(RUNNER)
        with mock.patch.dict(os.environ, {}, clear=True):
            exec(compile(RUNNER.read_bytes(), str(RUNNER), "exec"), module.__dict__)
        self.assertEqual(module.CLAUDE_MODEL, "claude-opus-5-5")
        self.assertEqual(module.CODEX_MODEL, "gpt-6.1-sol")
        self.assertEqual(module.CLAUDE_EFFORT, "xhigh")
        self.assertEqual(module.CODEX_REASONING_EFFORT, "xhigh")

    def test_explicit_overrides_preserve_ids_efforts_and_local_provenance(self):
        environment = {
            "CONSENSUS_CLAUDE_MODEL": "claude-explicit-test",
            "CONSENSUS_CODEX_MODEL": "gpt-explicit-test",
            "CONSENSUS_CLAUDE_EFFORT": "high",
            "CONSENSUS_CODEX_REASONING_EFFORT": "high",
        }
        with mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch.object(self.runner, "CLAUDE_MODEL", "claude-explicit-test"), \
                mock.patch.object(self.runner, "CODEX_MODEL", "gpt-explicit-test"), \
                mock.patch.object(self.runner, "CLAUDE_EFFORT", "high"), \
                mock.patch.object(self.runner, "CODEX_REASONING_EFFORT", "high"), \
                mock.patch.object(self.runner, "read_model_doc",
                                  side_effect=AssertionError("override discovery is forbidden")):
            selection = self.helper("resolve_models")()
        for provider, expected in (("claude", "claude-explicit-test"),
                                   ("openai", "gpt-explicit-test")):
            with self.subTest(provider=provider):
                selected = selection["models"][provider]
                self.assertEqual(selected["model"], expected)
                self.assertEqual(selected["effort"], "high")
                self.assertIn("override", selected["source"].lower())


class SymmetricProtocolTests(RunnerCase):
    def test_both_model_prompts_require_six_fields_and_canonical_progress(self):
        for me, other in (("Claude", "Codex"), ("Codex", "Claude")):
            with self.subTest(model=me):
                prompt = self.helper("build_prompt")(
                    me, other, "Check the claim.", [], True,
                )
                for field in FIELDS:
                    self.assertIn(field + ":", prompt)
                self.assertIn("NEW:", prompt)
                self.assertIn("NONE", prompt)
                self.assertNotIn("Claude checkpoint format:", prompt)

    def test_missing_checkpoint_cannot_converge_for_either_participant(self):
        for missing in ("Claude", "Codex"):
            with self.subTest(model=missing):
                progress = self.root / f"{missing}.progress.md"
                save = self.root / f"{missing}.md"
                claude = report() if missing != "Claude" else f"{ANSWER}\nVERDICT: AGREE"
                codex = report() if missing != "Codex" else f"{ANSWER}\nVERDICT: AGREE"
                selection = {
                    "resolved_at": "2026-10-07T00:00:00",
                    "selection_policy": "human-authorized fixed defaults",
                    "models": {
                        "claude": {"model": "claude-opus-5-5", "effort": "xhigh",
                                   "source": "fixed skill default"},
                        "openai": {"model": "gpt-6.1-sol", "effort": "xhigh",
                                   "source": "fixed skill default"},
                    },
                }
                output, errors = io.StringIO(), io.StringIO()
                argv = [str(RUNNER), "--quiet", "--max-rounds", "1",
                        "--no-synthesize", "--save", str(save),
                        "--progress", str(progress), "offline fixture"]
                with ExitStack() as stack:
                    stack.enter_context(mock.patch.object(sys, "argv", argv))
                    stack.enter_context(mock.patch.object(self.runner, "resolve_models",
                                                         return_value=selection))
                    stack.enter_context(mock.patch.object(self.runner, "run_claude",
                                                         return_value=("", claude)))
                    stack.enter_context(mock.patch.object(self.runner, "run_codex",
                                                         return_value=codex))
                    stack.enter_context(redirect_stdout(output))
                    stack.enter_context(redirect_stderr(errors))
                    try:
                        self.helper("main")()
                    except SystemExit as error:
                        self.assertNotEqual(error.code, 0)
                self.assertNotIn("CONSENSUS REACHED", output.getvalue())
                if save.exists():
                    self.assertNotIn("CONSENSUS REACHED", save.read_text())


class CheckpointMonitorTests(RunnerCase):
    def test_identical_checkpoints_in_distinct_completed_messages_are_observed(self):
        clock = [0.0]
        monitor = self.monitor(clock)
        observe = self.helper("stream_observer")("Claude", monitor)
        for index in (1, 2):
            clock[0] += 900
            monitor.tick()
            self.assertEqual(monitor.misses, 1)
            event = assistant_event(checkpoint("NONE"))
            event["message"]["id"] = "message-" + str(index)
            observe("stdout", json.dumps(event) + "\n")
            self.assertEqual(monitor.misses, 0)

    def test_late_checkpoint_cannot_resurrect_two_miss_stop(self):
        clock = [0.0]
        monitor = self.monitor(clock)
        clock[0] = 1800.0
        with self.assertRaises(RuntimeError):
            monitor.tick()
        monitor.observe(self.helper("checkpoint_of")(checkpoint()))
        self.assertEqual(monitor.misses, 2)
        clock[0] = 1801.0
        with self.assertRaises(RuntimeError):
            monitor.tick()

    def monitor(self, clock):
        self.guards.enter_context(mock.patch.object(self.runner.time, "monotonic",
                                                   side_effect=lambda: clock[0]))
        monitor = self.helper("CheckpointMonitor")(interval=900)
        monitor.tick()
        return monitor

    def test_one_missed_deadline_is_not_terminal(self):
        clock = [0.0]
        monitor = self.monitor(clock)
        clock[0] = 900.0
        monitor.tick()
        self.assertEqual(monitor.misses, 1)

    def test_two_consecutive_missed_deadlines_are_terminal(self):
        clock = [0.0]
        monitor = self.monitor(clock)
        clock[0] = 900.0
        monitor.tick()
        clock[0] = 1800.0
        with self.assertRaisesRegex(RuntimeError, "checkpoint|semantic"):
            monitor.tick()

    def test_arbitrary_stream_activity_does_not_satisfy_checkpoint_deadline(self):
        clock = [0.0]
        monitor = self.monitor(clock)
        clock[0] = 899.0
        monitor.observe(self.helper("checkpoint_of")("stdout status: still working"))
        clock[0] = 900.0
        monitor.tick()
        clock[0] = 1799.0
        monitor.observe(self.helper("checkpoint_of")("stderr heartbeat"))
        clock[0] = 1800.0
        with self.assertRaisesRegex(RuntimeError, "checkpoint|semantic"):
            monitor.tick()

    def test_valid_checkpoint_resets_consecutive_missed_deadlines(self):
        clock = [0.0]
        monitor = self.monitor(clock)
        clock[0] = 900.0
        monitor.tick()
        clock[0] = 901.0
        monitor.tick()
        monitor.observe(self.helper("checkpoint_of")(checkpoint()))
        clock[0] = 1801.0
        monitor.tick()
        self.assertEqual(monitor.misses, 1)
        clock[0] = 2701.0
        with self.assertRaisesRegex(RuntimeError, "checkpoint|semantic"):
            monitor.tick()

    def test_time_while_participant_paused_does_not_count_as_missed_deadlines(self):
        clock = [0.0]
        monitor = self.monitor(clock)
        clock[0] = 900.0
        monitor.pause()
        self.assertEqual(monitor.misses, 1)
        clock[0] = 9000.0
        monitor.tick()
        self.assertEqual(monitor.misses, 1)
        clock[0] = 9900.0
        with self.assertRaisesRegex(RuntimeError, "checkpoint|semantic"):
            monitor.tick()

    def test_invalid_checkpoint_does_not_reset_deadlines(self):
        clock = [0.0]
        monitor = self.monitor(clock)
        clock[0] = 900.0
        monitor.tick()
        clock[0] = 901.0
        monitor.observe(self.helper("checkpoint_of")(checkpoint("uncategorized progress")))
        clock[0] = 1800.0
        with self.assertRaisesRegex(RuntimeError, "checkpoint|semantic"):
            monitor.tick()


class ShutdownLatchTests(RunnerCase):
    """Termination signals latch a stop; they never raise asynchronously."""

    def setUp(self):
        super().setUp()
        self.helper("shutdown_requested")
        state = self.runner._SHUTDOWN
        saved = dict(state)
        handlers = {s: signal.getsignal(s) for s in self.runner.TERMINATION_SIGNALS}

        def restore():
            state.clear()
            state.update(saved)
            for s, handler in handlers.items():
                signal.signal(s, handler)
        self.addCleanup(restore)
        state.update({"requested": False, "signal": None})

    def test_installed_handler_latches_without_raising(self):
        self.helper("install_shutdown_handlers")()
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.05)  # the handler runs at the next bytecode boundary
        self.assertTrue(self.runner.shutdown_requested())
        self.assertEqual(self.runner._SHUTDOWN["signal"], signal.SIGTERM)
        with self.assertRaises(KeyboardInterrupt) as raised:
            self.runner.check_shutdown()
        self.assertIsInstance(raised.exception, self.runner.ShutdownRequested)

    def test_signal_during_deferred_cleanup_is_latched_not_dropped(self):
        calls = []

        def original(*_):
            calls.append("original")
        signal.signal(signal.SIGTERM, original)
        with self.helper("deferred_interrupts")():
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.05)
        self.assertEqual(calls, [], "cleanup must not run the interrupting handler")
        self.assertTrue(self.runner.shutdown_requested())
        self.assertIs(signal.getsignal(signal.SIGTERM), original)

    def test_streaming_refuses_to_spawn_after_stop(self):
        # setUp turns any Popen into an AssertionError, so reaching it fails.
        self.runner._latch_shutdown(signal.SIGTERM)
        with self.assertRaises(self.runner.ShutdownRequested):
            self.runner._run_streaming(["claude", "-p"], {}, 5)

    def test_signaled_provider_exit_codes_are_classified(self):
        exit_error = self.helper("provider_exit_error")
        signaled = self.runner.ProviderSignaled
        for code in (-signal.SIGTERM, -signal.SIGKILL, 128 + signal.SIGTERM,
                     128 + signal.SIGINT, 128 + signal.SIGHUP):
            self.assertIsInstance(exit_error("claude", code), signaled, code)
        for code in (1, 2, 3, 127):
            error = exit_error("codex", code)
            self.assertIsInstance(error, RuntimeError)
            self.assertNotIsInstance(error, signaled, code)
            self.assertIn("codex exited with status", str(error))

    def test_termination_pending_waits_briefly_only_for_signaled_providers(self):
        pending = self.helper("termination_pending")
        with mock.patch.object(self.runner, "PROVIDER_SIGNAL_GRACE", 0.1):
            started = time.monotonic()
            self.assertFalse(pending(RuntimeError("claude exited with status 1")))
            self.assertLess(time.monotonic() - started, 0.05)
            self.assertFalse(pending(self.runner.ProviderSignaled("claude exited")))
            self.assertGreaterEqual(time.monotonic() - started, 0.1)
            # The runner's own signal arriving inside the grace window counts.
            timer = threading.Timer(0.02, self.runner._latch_shutdown, (signal.SIGTERM,))
            self.addCleanup(timer.cancel)
            timer.start()
            self.assertTrue(pending(self.runner.ProviderSignaled("claude exited")))
            self.assertTrue(pending(ValueError("invalid report")))


if __name__ == "__main__":
    unittest.main()
