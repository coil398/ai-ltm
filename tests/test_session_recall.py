from __future__ import annotations

from contextlib import closing, redirect_stderr
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "session_recall.py"
SPEC = importlib.util.spec_from_file_location("standalone_session_recall", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SESSION_RECALL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SESSION_RECALL
SPEC.loader.exec_module(SESSION_RECALL)


class SessionRecallCandidateTests(unittest.TestCase):
    def _fixture(self):
        temporary_directory = tempfile.TemporaryDirectory()
        root = Path(temporary_directory.name)
        repo = root / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        db = root / "memory.db"
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute("CREATE TABLE episodes (id INTEGER PRIMARY KEY, context TEXT)")
            connection.executemany(
                "INSERT INTO episodes(id, context) VALUES (?, ?)",
                [(index, "C" * 700) for index in range(1, 8)],
            )
        return temporary_directory, root, repo, db

    @staticmethod
    def _runner(repo, db, events, *, search_timeout=3.0):
        return SESSION_RECALL.RecallRunner(
            repo=repo,
            db=db,
            query="synthetic current task",
            summary="synthetic summary",
            vector_search=repo / "vector_search.py",
            search_timeout=search_timeout,
            emit=events.append,
        )

    def test_terminal_returns_all_ids_and_bounded_top_five_without_database_writes(self):
        fixture = self._fixture()
        with fixture[0]:
            _, _, repo, db = fixture
            records = [
                {
                    "id": index,
                    "summary": "S" * 1400,
                    "tags": "T" * 400,
                    "combined_score": 1 - index / 10,
                }
                for index in range(1, 8)
            ]
            before = (hashlib.sha256(db.read_bytes()).hexdigest(), db.stat().st_mtime_ns)
            events = []
            runner = self._runner(repo, db, events)
            with patch.dict(os.environ, {}, clear=True), patch.object(
                SESSION_RECALL, "run_bounded",
                return_value=SESSION_RECALL.CommandResult(0, json.dumps(records)),
            ):
                self.assertEqual(runner._search(), "completed")
                self.assertEqual(runner._finish("completed"), 0)

            terminal = events[-1]
            self.assertEqual(terminal["type"], "terminal")
            self.assertEqual(terminal["result_ids"], list(range(1, 8)))
            self.assertEqual([item["id"] for item in terminal["results"]], [1, 2, 3, 4, 5])
            self.assertEqual(len(terminal["results"]), 5)
            self.assertEqual(len(terminal["results"][0]["summary"]), 1200)
            self.assertEqual(len(terminal["results"][0]["context"]), 600)
            self.assertEqual(len(terminal["results"][0]["tags"]), 300)
            self.assertEqual(terminal["results"][0]["score"], 0.9)
            self.assertEqual(
                before,
                (hashlib.sha256(db.read_bytes()).hexdigest(), db.stat().st_mtime_ns),
            )

    def test_optional_annotation_observes_key_and_remaining_search_budget(self):
        cases = (
            ("missing key", "", 10.5, False, False),
            ("blank key", " \t ", 10.5, False, False),
            ("annotated", "mock-key", 10.5, True, True),
            ("service failure", "mock-key", 10.5, True, False),
            ("exhausted budget", "mock-key", 14.0, False, False),
        )
        for label, key, search_finished_at, should_call, should_annotate in cases:
            with self.subTest(label=label):
                fixture = self._fixture()
                with fixture[0]:
                    _, _, repo, db = fixture
                    events = []
                    runner = self._runner(repo, db, events)
                    records = [{"id": 1, "summary": "Synthetic memory", "combined_score": 0.8}]
                    bridge = Mock()
                    observed = {}

                    def annotate(query, candidates, *, environ=None, timeout_s=None):
                        observed["query"] = query
                        observed["timeout_s"] = timeout_s
                        if label == "service failure":
                            raise RuntimeError("mock failure")
                        return [
                            dict(item, jev_annotation={"relevance": "relevant"})
                            for item in candidates
                        ]

                    bridge.annotate.side_effect = annotate
                    with patch.dict(os.environ, {"TYPESAFE_API_KEY": key}, clear=True), \
                         patch.dict(sys.modules, {"jev_bridge": bridge}), \
                         patch.object(
                             SESSION_RECALL,
                             "run_bounded",
                             return_value=SESSION_RECALL.CommandResult(0, json.dumps(records)),
                         ), \
                         patch.object(
                             SESSION_RECALL.time,
                             "monotonic",
                             side_effect=[10.0, search_finished_at],
                         ), \
                         redirect_stderr(io.StringIO()):
                        self.assertEqual(runner._search(), "completed")

                    self.assertEqual(bridge.annotate.call_count, int(should_call))
                    self.assertEqual("jev_annotation" in runner.state["results"][0], should_annotate)
                    if should_call:
                        self.assertEqual(observed["query"], "synthetic current task")
                        self.assertGreater(observed["timeout_s"], 0)
                        self.assertLess(observed["timeout_s"], 3.0)


if __name__ == "__main__":
    unittest.main()
