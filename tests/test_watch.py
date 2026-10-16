"""Watcher policy tests. Nothing here starts Claude or calls the hub."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import claude_mail_watch as watch


def _msg(mid, sender, subject="hello", body=""):
    return {"id": mid, "from_agent": sender, "subject": subject, "body": body, "created_at": mid}


class WatchTests(unittest.TestCase):
    def test_classify_keeps_jobs_ahead_of_peer_mail(self):
        messages = [
            _msg("1", "operator", "BUILD: ship it"),
            _msg("2", "grok", "status"),
            _msg("3", "operator", "just a note"),
            _msg("4", "claude-heavy", "echo"),
            _msg("5", "sentinel", "TASK: ignore me"),
            {"from_agent": "grok", "subject": "no id"},
        ]
        jobs, triage = watch.classify_messages(messages, {"5"}, me="claude-heavy")
        self.assertEqual([m["id"] for m in jobs], ["1"])
        self.assertEqual([m["id"] for m in triage], ["2"])

    def test_subject_and_slug(self):
        match = watch.SUBJECT_RE.match("  task: Fix the door ")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(2).strip(), "Fix the door")
        self.assertIsNone(watch.SUBJECT_RE.match("build the door"))
        self.assertEqual(watch.slugify("Hello, World!!!"), "hello-world")
        self.assertEqual(watch.slugify("***"), "job")

    def test_resolve_workdir_pins_existing_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            pinned = watch.resolve_workdir("title", f"repo: {repo}\nrest", root / "builds")
            self.assertEqual(pinned, repo)
            fresh = watch.resolve_workdir("My Job", "do the work", root / "builds")
            self.assertEqual(fresh, root / "builds" / "my-job")
            self.assertTrue(fresh.is_dir())

    def test_lock_and_triage_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "lock"
            self.assertTrue(watch.acquire_lock(lock))
            self.assertFalse(watch.acquire_lock(lock))
            watch.release_lock(lock)
            self.assertTrue(watch.acquire_lock(lock))
            watch.release_lock(lock)

            state = Path(tmp) / "triaged.json"
            watch.save_triaged(state, {"a", "b"})
            self.assertEqual(watch.load_triaged(state), {"a", "b"})
            state.write_text("{", encoding="utf-8")
            self.assertEqual(watch.load_triaged(state), set())

    def test_load_config_uses_file_env_not_the_network(self):
        saved = os.environ.pop("CLAUDE_IM_ID", None)
        saved_cli = os.environ.pop("CLAUDE_CLI", None)
        try:
            cfg = watch.load_config({
                "CLAUDE_IM_ID": "claude-command",
                "CLAUDE_WATCH_TIMEOUT_MIN": "15",
                "CLAUDE_WATCH_MAX_TURNS": "nope",
                "CLAUDE_CLI": str(Path(__file__)),
            })
        finally:
            if saved is not None:
                os.environ["CLAUDE_IM_ID"] = saved
            if saved_cli is not None:
                os.environ["CLAUDE_CLI"] = saved_cli
        self.assertEqual(cfg.me, "claude-command")
        self.assertEqual(cfg.timeout_min, 15)
        self.assertEqual(cfg.max_turns, 200)
        self.assertEqual(cfg.claude_cli, str(Path(__file__)))

    def test_current_pid_is_alive(self):
        self.assertTrue(watch.pid_alive(os.getpid()))
        self.assertFalse(watch.pid_alive(0))


if __name__ == "__main__":
    unittest.main()
