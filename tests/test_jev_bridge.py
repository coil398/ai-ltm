from __future__ import annotations

from contextlib import redirect_stderr
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "jev_bridge.py"
SPEC = importlib.util.spec_from_file_location("standalone_jev_bridge", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
JEV_BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(JEV_BRIDGE)


class BridgeSourceDiscoveryTests(unittest.TestCase):
    def test_shallow_script_path_without_package_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "jev_bridge.py"
            with patch.object(JEV_BRIDGE, "__file__", str(script)):
                self.assertIsNone(JEV_BRIDGE._hooks_source({}))

    def test_uses_jev_hooks_found_under_an_actual_ancestor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            script = root / "standalone/skills/ai-ltm/scripts/jev_bridge.py"
            script.parent.mkdir(parents=True)
            memory = root / "jev-hooks/src/jev_hooks/memory.py"
            memory.parent.mkdir(parents=True)
            memory.touch()
            with patch.object(JEV_BRIDGE, "__file__", str(script)):
                self.assertEqual(JEV_BRIDGE._hooks_source({}), root / "jev-hooks/src")

    def test_explicit_root_is_used_without_falling_back_to_an_ancestor(self):
        with tempfile.TemporaryDirectory() as tmp:
            explicit_root = Path(tmp) / "empty-hooks"
            explicit_root.mkdir()
            with patch.object(JEV_BRIDGE, "__file__", str(SCRIPT)):
                self.assertIsNone(
                    JEV_BRIDGE._hooks_source({"JEV_HOOKS_ROOT": str(explicit_root)})
                )


class BridgeAnnotationTests(unittest.TestCase):
    def setUp(self):
        self.results = [
            {
                "id": index,
                "summary": "s" * 1300,
                "context": "c" * 1300,
                "tags": "t" * 400,
                "combined_score": index / 10,
                "private_metadata": "local only",
            }
            for index in range(1, 7)
        ]

    def test_missing_or_whitespace_key_never_loads_jev(self):
        for environ in ({}, {"TYPESAFE_API_KEY": " \t\n "}):
            with self.subTest(environ=environ):
                with patch.object(JEV_BRIDGE, "_load_memory") as load, redirect_stderr(io.StringIO()):
                    returned = JEV_BRIDGE.annotate("query", self.results, environ=environ)
                load.assert_not_called()
                self.assertIs(returned, self.results)

    def test_missing_package_keeps_candidates(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stderr(io.StringIO()):
            returned = JEV_BRIDGE.annotate(
                "query",
                self.results,
                environ={"TYPESAFE_API_KEY": "mock-key", "JEV_HOOKS_ROOT": tmp},
            )
        self.assertIs(returned, self.results)

    def test_annotation_sends_only_bounded_fields_and_preserves_results(self):
        class FakeMemory:
            call = None

            @classmethod
            def annotate(cls, query, candidates, *, environ=None, timeout_s=None):
                cls.call = (query, candidates, environ, timeout_s)
                return [
                    {**candidate, "jev_annotation": {"relevance": "relevant"}}
                    for candidate in candidates
                ]

        with patch.object(JEV_BRIDGE, "_load_memory", return_value=FakeMemory):
            annotated = JEV_BRIDGE.annotate(
                "q" * 1300,
                self.results,
                environ={"TYPESAFE_API_KEY": "mock-key"},
                timeout_s=0.75,
            )

        query, sent, _environ, timeout_s = FakeMemory.call
        self.assertEqual(len(query), 1200)
        self.assertEqual(timeout_s, 0.75)
        self.assertEqual(len(sent), 5)
        self.assertEqual(set(sent[0]), {"summary", "context", "tags", "score"})
        self.assertEqual(len(sent[0]["summary"]), 1200)
        self.assertEqual(len(sent[0]["context"]), 1200)
        self.assertEqual(len(sent[0]["tags"]), 300)
        self.assertNotIn("id", sent[0])
        self.assertNotIn("private_metadata", sent[0])
        self.assertEqual([item["id"] for item in annotated], [1, 2, 3, 4, 5, 6])
        self.assertEqual([item["id"] for item in self.results], [1, 2, 3, 4, 5, 6])
        for before, after in zip(self.results[:5], annotated[:5]):
            self.assertEqual({key: after[key] for key in before}, before)
            self.assertEqual(after["jev_annotation"], {"relevance": "relevant"})
        self.assertEqual(annotated[5], self.results[5])

    def test_runtime_failure_keeps_candidates_and_does_not_echo_response(self):
        class BrokenMemory:
            @staticmethod
            def annotate(*args, **kwargs):
                raise RuntimeError("private response body")

        error = io.StringIO()
        with patch.object(JEV_BRIDGE, "_load_memory", return_value=BrokenMemory), redirect_stderr(error):
            returned = JEV_BRIDGE.annotate(
                "query", self.results, environ={"TYPESAFE_API_KEY": "mock-key"}
            )
        self.assertIs(returned, self.results)
        self.assertIn("RuntimeError", error.getvalue())
        self.assertNotIn("private response body", error.getvalue())

    def test_invalid_annotation_keeps_candidates(self):
        class InvalidMemory:
            @staticmethod
            def annotate(query, candidates, *, environ=None, timeout_s=None):
                return [{**candidates[0], "summary": "changed", "jev_annotation": {}}]

        with patch.object(JEV_BRIDGE, "_load_memory", return_value=InvalidMemory), redirect_stderr(io.StringIO()):
            returned = JEV_BRIDGE.annotate(
                "query", self.results, environ={"TYPESAFE_API_KEY": "mock-key"}
            )
        self.assertIs(returned, self.results)


if __name__ == "__main__":
    unittest.main()
