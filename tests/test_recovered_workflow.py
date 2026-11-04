"""The reviewed workflow builds candidates without publication or a timer."""

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

    def test_no_public_write_route(self):
        workflow = (ROOT / ".github/workflows/recovered-refresh.yml").read_text()
        for forbidden in (
            "contents: write",
            "pull-requests: write",
            "git push",
            "gh pr create",
            "pull_request_target",
        ):
            self.assertNotIn(forbidden, workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn("environment: parking-source-main", workflow)
        self.assertIn("PARKING_SOURCE_READ_TOKEN", workflow)
        self.assertIn("PARKING_RECOVERED_AGE_IDENTITY", workflow)
        self.assertIn("if: always()", workflow)
