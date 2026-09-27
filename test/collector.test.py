#!/usr/bin/env python3
"""Collector tests.

Each test runs collector.py in a throwaway HOME with a PATH holding only
stub commands, so nothing touches real agent data or calls `omarchy`.
"""

import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

COLLECTOR = Path(__file__).resolve().parent.parent / "collector.py"


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.state = self.home / "state"
        self.data = self.home / "data"

    def tearDown(self):
        self._tmp.cleanup()

    def stub(self, name):
        path = self.bin / name
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)

    def collect(self, *args):
        env = {
            "HOME": str(self.home),
            "PATH": str(self.bin),
            "XDG_STATE_HOME": str(self.state),
            "XDG_DATA_HOME": str(self.data),
        }
        result = subprocess.run([sys.executable, str(COLLECTOR), *args], env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def record(self, agent_id):
        path = self.state / "omarchy/agent-monitor/usage" / f"{agent_id}.json"
        return json.loads(path.read_text())

    def providers(self):
        return json.loads((self.home / ".config/omarchy/agent-monitor/providers.json").read_text())

    def test_writes_a_record_for_every_known_agent(self):
        self.collect()
        written = {p.stem for p in (self.state / "omarchy/agent-monitor/usage").glob("*.json")}
        self.assertIn("claude", written)
        self.assertIn("codex", written)
        self.assertIn("opencode", written)
        self.assertNotIn("gemini", written)

    def test_missing_agent_is_reported_not_installed(self):
        self.collect("aider")
        record = self.record("aider")
        self.assertEqual(record["installationStatus"], "Not installed")
        self.assertFalse(record["installed"])
        self.assertFalse(record["monitoringEnabled"])

    def test_account_services_do_not_need_a_command(self):
        self.collect("fireworks")
        self.assertEqual(self.record("fireworks")["installationStatus"], "Account service")

    def test_set_enabled_persists_and_disables_monitoring(self):
        self.stub("aider")
        self.collect("--set-enabled", "aider", "false")
        self.assertEqual(self.providers()["aider"], {"enabled": False})
        record = self.record("aider")
        self.assertFalse(record["monitoringEnabled"])
        self.assertEqual(record["usageStatusText"], "Monitoring disabled")
        self.assertTrue(record["installed"])

        self.collect("--set-enabled", "aider", "true")
        self.assertEqual(self.providers()["aider"], {"enabled": True})
        self.assertNotEqual(self.record("aider")["usageStatusText"], "Monitoring disabled")

    def test_set_enabled_rejects_unknown_agents(self):
        env = {"HOME": str(self.home), "PATH": str(self.bin), "XDG_STATE_HOME": str(self.state)}
        result = subprocess.run([sys.executable, str(COLLECTOR), "--set-enabled", "nope", "true"],
                                env=env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / ".config/omarchy/agent-monitor/providers.json").exists())

    def test_pi_sessions_are_counted(self):
        self.stub("pi")
        sessions = self.home / ".pi/agent/sessions"
        sessions.mkdir(parents=True)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        lines = [
            {"type": "message", "timestamp": now, "message": {"role": "user"}},
            {"type": "message", "timestamp": now, "message": {
                "role": "assistant", "model": "test-model",
                "usage": {"input": 100, "output": 20, "cacheRead": 5, "cacheWrite": 1}}},
            {"type": "session", "timestamp": now},
        ]
        (sessions / "a.jsonl").write_text("\n".join(json.dumps(l) for l in lines) + "\nnot json\n")

        self.collect("pi")
        record = self.record("pi")
        self.assertEqual(record["installationStatus"], "Installed")
        self.assertEqual(record["totalSessions"], 1)
        self.assertEqual(record["todayPrompts"], 1)
        self.assertEqual(record["todayTotalTokens"], 126)
        self.assertEqual(record["modelUsage"]["test-model"], {
            "inputTokens": 100, "outputTokens": 20,
            "cacheReadInputTokens": 5, "cacheCreationInputTokens": 1})
        self.assertEqual(len(record["recentDays"]), 7)

    def test_opencode_database_is_read_only_and_counted(self):
        self.stub("opencode")
        db = self.data / "opencode/opencode.db"
        db.parent.mkdir(parents=True)
        created = int(dt.datetime.now().timestamp() * 1000)
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE message (session_id TEXT, data TEXT)")
            conn.execute("INSERT INTO message VALUES (?, ?)", ("s1", json.dumps(
                {"role": "user", "time": {"created": created}})))
            conn.execute("INSERT INTO message VALUES (?, ?)", ("s1", json.dumps({
                "role": "assistant", "modelID": "oc-model", "time": {"created": created},
                "tokens": {"input": 10, "output": 4, "reasoning": 2, "cache": {"read": 3, "write": 0}}})))
        before = db.stat().st_mtime_ns

        self.collect("opencode")
        record = self.record("opencode")
        self.assertEqual(record["totalPrompts"], 1)
        self.assertEqual(record["todayTotalTokens"], 19)
        self.assertEqual(record["modelUsage"]["oc-model"]["outputTokens"], 6)
        self.assertEqual(db.stat().st_mtime_ns, before)

    def test_installed_agent_without_usage_source_says_so(self):
        self.stub("opencode")
        self.collect("opencode")
        self.assertEqual(self.record("opencode")["usageStatusText"], "Installed · usage unavailable")

    def test_installed_agent_with_empty_history_says_so(self):
        self.stub("opencode")
        db = self.data / "opencode/opencode.db"
        db.parent.mkdir(parents=True)
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE message (session_id TEXT, data TEXT)")
        self.collect("opencode")
        self.assertEqual(self.record("opencode")["usageStatusText"], "Installed · no recorded sessions")

    def test_mise_launcher_without_install_is_launcher_only(self):
        launcher = self.bin / "aider"
        launcher.write_text('#!/bin/sh\nexec mise use -g --quiet "pipx:aider-chat"\n')
        launcher.chmod(0o755)
        self.collect("aider")
        self.assertEqual(self.record("aider")["installationStatus"], "Launcher only")

    def test_openrouter_without_a_key_writes_nothing(self):
        self.collect("--set-enabled", "openrouter", "true")
        self.assertFalse((self.state / "omarchy/agent-monitor/usage/openrouter.json").exists())


if __name__ == "__main__":
    unittest.main()
