"""The parked workflow cannot access private source or publish output."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class RecoveredWorkflowTests(unittest.TestCase):
    def test_synthetic_reducer_replay(self):
        spec = importlib.util.spec_from_file_location(
            "dry_run", ROOT / "scripts/recovered_refresh_dry_run.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.run()["case_identifiers"], 1)

    def test_no_sensitive_or_public_write_route(self):
        workflow = (ROOT / ".github/workflows/recovered-refresh.yml").read_text()
        for forbidden in (
            "secrets.",
            "environment:",
            "schedule:",
            "pull_request",
            "contents: write",
            "upload-artifact",
            "git push",
            "repository_dispatch",
        ):
            self.assertNotIn(forbidden, workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("workflow_dispatch:", workflow)
