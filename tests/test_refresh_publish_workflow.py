"""Static guarantees of the scheduled refresh-and-publish workflow."""

import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
TEXT = (ROOT / ".github/workflows/pages.yml").read_text()
REFRESH, DEPLOY = TEXT.split("\n  deploy:")


class RefreshPublishWorkflowTests(unittest.TestCase):
    def test_schedule_dispatch_and_push_triggers(self):
        self.assertIn("schedule:", TEXT)
        self.assertIn("cron: '17 */6 * * *'", TEXT)
        self.assertIn("workflow_dispatch:", TEXT)
        self.assertIn("branches: [main]", TEXT)
        self.assertNotIn("pull_request", TEXT)

    def test_secrets_only_in_refresh_job_on_trusted_main(self):
        self.assertIn("environment: parking-source-main", REFRESH)
        self.assertIn("github.ref == 'refs/heads/main'", REFRESH)
        self.assertIn("github.repository == 'reihlinstinct/parking-stats'", REFRESH)
        self.assertNotIn("secrets.", DEPLOY.replace("secrets.GITHUB_TOKEN", ""))
        self.assertIn("PARKING_SOURCE_READ_TOKEN", REFRESH)
        self.assertIn("PARKING_RECOVERED_AGE_IDENTITY", REFRESH)

    def test_no_repository_write_route(self):
        for forbidden in (
            "contents: write",
            "pull-requests: write",
            "git push",
            "gh pr",
            "pull_request_target",
            "persist-credentials: true",
        ):
            self.assertNotIn(forbidden, TEXT)

    def test_deploy_fails_closed_and_overlays_only_three_files(self):
        self.assertIn("needs: refresh", DEPLOY)
        self.assertIn("needs.refresh.result == 'success'", DEPLOY)
        self.assertIn("!cancelled()", DEPLOY)
        for name in (
            "data/recovered-summary.json",
            "data/source-freshness.json",
            "index.html",
        ):
            self.assertIn(name, DEPLOY)
        self.assertIn("unexpected contents", DEPLOY)
        self.assertIn("privacy_scan", DEPLOY)
        self.assertIn("artifact_gate", DEPLOY)

    def test_artifact_is_aggregate_only_and_short_lived(self):
        self.assertIn("retention-days: 1", REFRESH)
        self.assertIn("if-no-files-found: error", REFRESH)
        self.assertEqual(REFRESH.count("public-repo/public/"), 7)
        self.assertIn("Always remove private material", REFRESH)
        self.assertIn("if: always()", REFRESH)

    def test_all_actions_are_sha_pinned(self):
        for line in TEXT.splitlines():
            if line.strip().startswith(("- uses:", "uses:")):
                self.assertRegex(line, r"@[0-9a-f]{40} # ")


if __name__ == "__main__":
    unittest.main()
