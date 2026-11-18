"""Static guarantees of the publication hold and run-boundary wiring (#36)."""

import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
TEXT = (ROOT / ".github/workflows/pages.yml").read_text()
REFRESH, DEPLOY = TEXT.split("\n  deploy:")


class PublishPipelineGuardTests(unittest.TestCase):
    def test_publisher_runs_serialize(self):
        self.assertIn("cancel-in-progress: false", TEXT)
        self.assertNotIn("cancel-in-progress: true", TEXT)

    def test_filter_start_records_main_and_checks_hold(self):
        self.assertIn("id: boundary", REFRESH)
        self.assertIn("record-main --repo public-repo", REFRESH)
        self.assertIn("check-hold --public-root public-repo", REFRESH)
        self.assertIn("main_at_start", REFRESH)
        self.assertLess(
            REFRESH.index("check-hold"), REFRESH.index("fetch_parking_source")
        )

    def test_deploy_rechecks_hold_from_current_main_before_write(self):
        self.assertIn("check-hold --repo . --ref origin/main", DEPLOY)
        self.assertIn("verify-main --repo . --expected", DEPLOY)
        self.assertIn("needs.refresh.outputs.main_at_start", DEPLOY)
        self.assertIn("+refs/heads/main:refs/remotes/origin/main", DEPLOY)
        self.assertLess(
            DEPLOY.index("check-hold"), DEPLOY.index("upload-pages-artifact")
        )

    def test_boundary_recording_fails_closed(self):
        # A standalone assignment exits the step under set -e when record-main
        # fails; the fail-open pattern embeds the substitution in echo.
        self.assertIn(
            "MAIN_AT_START=$(python -m parking_stats.publish_guard record-main", TEXT
        )
        self.assertNotIn('echo "main_at_start=$(python', TEXT)

    def test_deploy_aborts_on_empty_boundary_after_successful_refresh(self):
        self.assertIn("REFRESH_RESULT: ${{ needs.refresh.result }}", DEPLOY)
        self.assertIn('if [ "$REFRESH_RESULT" = "success" ]; then', DEPLOY)
        self.assertIn('test -n "$MAIN_AT_START"', DEPLOY)

    def test_hold_check_precedes_secret_use(self):
        self.assertLess(
            REFRESH.index("check-hold"), REFRESH.index("PARKING_SOURCE_READ_TOKEN")
        )


if __name__ == "__main__":
    unittest.main()
