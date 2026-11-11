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


# ---- Parsed-workflow tests (PyYAML): conditions, step order, overlay script ----
import re  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

import yaml  # noqa: E402

DOC = yaml.safe_load(TEXT)
JOBS = DOC["jobs"]
UPSTREAM = "reihlinstinct/parking-stats"


def squash(text):
    return " ".join(str(text).split())


def evaluate(expression, **context):
    """Evaluate the small GitHub expression subset the deploy condition uses."""
    names = {
        "needs.refresh.result": "needs_refresh_result",
        "github.repository": "github_repository",
        "github.event_name": "github_event_name",
        "inputs.refresh": "inputs_refresh",
    }
    text = squash(expression)
    text = text.replace("!cancelled()", "True")
    text = text.replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"!\s*(inputs\.refresh)", r"not \1", text)
    for dotted, variable in names.items():
        text = text.replace(dotted, variable)
    return bool(eval(text, {"__builtins__": {}}, dict(context)))  # noqa: S307


def step(job, name):
    matches = [s for s in JOBS[job]["steps"] if s.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def step_names(job):
    return [
        s.get("name") or s.get("uses", "").split("@")[0] for s in JOBS[job]["steps"]
    ]


class ParsedWorkflowTests(unittest.TestCase):
    def test_triggers(self):
        triggers = DOC[True]  # YAML 1.1 parses the key `on` as True
        self.assertEqual(set(triggers), {"push", "schedule", "workflow_dispatch"})
        self.assertEqual(triggers["push"]["branches"], ["main"])
        self.assertEqual(triggers["schedule"], [{"cron": "17 */6 * * *"}])
        self.assertIs(
            triggers["workflow_dispatch"]["inputs"]["refresh"]["default"], True
        )
        self.assertEqual(DOC["permissions"], {"contents": "read"})

    def test_refresh_job_is_the_only_one_with_secrets_and_is_main_only(self):
        self.assertEqual(JOBS["refresh"]["environment"], "parking-source-main")
        self.assertEqual(JOBS["refresh"]["permissions"], {"contents": "read"})
        condition = squash(JOBS["refresh"]["if"])
        self.assertIn("github.ref == 'refs/heads/main'", condition)
        self.assertIn(f"github.repository == '{UPSTREAM}'", condition)
        deploy = yaml.safe_dump(JOBS["deploy"])
        self.assertNotIn("secrets.PARKING", deploy)
        self.assertEqual(
            JOBS["deploy"]["permissions"],
            {"contents": "read", "pages": "write", "id-token": "write"},
        )
        for job in JOBS.values():
            for item in job["steps"]:
                if item.get("uses", "").startswith("actions/checkout"):
                    self.assertIs(item["with"]["persist-credentials"], False)

    def test_deploy_condition_truth_table(self):
        condition = JOBS["deploy"]["if"]
        cases = [
            # refresh result, repository, event, refresh input -> deploys?
            ("success", UPSTREAM, "schedule", None, True),
            ("success", UPSTREAM, "push", None, True),
            ("failure", UPSTREAM, "schedule", None, False),
            ("failure", UPSTREAM, "push", None, False),
            ("failure", UPSTREAM, "workflow_dispatch", True, False),
            ("cancelled", UPSTREAM, "push", None, False),
            ("skipped", UPSTREAM, "push", None, False),
            ("skipped", UPSTREAM, "schedule", None, False),
            ("skipped", UPSTREAM, "workflow_dispatch", True, False),
            ("skipped", UPSTREAM, "workflow_dispatch", False, True),
            ("skipped", "someone/fork", "push", None, True),
        ]
        for result, repo, event, refresh, expected in cases:
            with self.subTest(result=result, repo=repo, event=event, refresh=refresh):
                self.assertIs(
                    evaluate(
                        condition,
                        needs_refresh_result=result,
                        github_repository=repo,
                        github_event_name=event,
                        inputs_refresh=refresh,
                    ),
                    expected,
                )

    def test_deploy_needs_refresh_and_overlay_steps_are_success_only(self):
        self.assertEqual(JOBS["deploy"]["needs"], "refresh")
        for name in (
            "Download refreshed files",
            "Overlay exactly the three refreshed files",
            "Re-validate the refreshed aggregates",
        ):
            self.assertEqual(
                squash(step("deploy", name)["if"]), "needs.refresh.result == 'success'"
            )

    def test_deploy_step_order_scan_before_publish(self):
        names = step_names("deploy")
        order = [
            "Download refreshed files",
            "Overlay exactly the three refreshed files",
            "Privacy-scan public/ before deploy",
            "Re-validate the refreshed aggregates",
            "actions/configure-pages",
            "actions/upload-pages-artifact",
            "actions/deploy-pages",
        ]
        positions = [names.index(n) for n in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("parking_stats.privacy_scan", step("deploy", order[2])["run"])
        self.assertIn("artifact_gate", step("deploy", order[3])["run"])

    def test_refresh_step_order_and_cleanup(self):
        names = step_names("refresh")
        order = [
            "Quiet fixed private-source fetch",
            "Build validated aggregates",
            "Render landing page from the aggregates",
            "Validate the three published files",
            "Upload aggregate-only site files",
            "Always remove private material",
        ]
        positions = [names.index(n) for n in order]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(step("refresh", order[-1])["if"], "always()")
        gate = step("refresh", order[3])["run"]
        for needle in ("privacy_scan", "diff_gate", "PUBLISHED", "artifact_gate"):
            self.assertIn(needle, gate)

    def test_artifact_paths_and_retention(self):
        upload = next(
            s
            for s in JOBS["refresh"]["steps"]
            if s.get("uses", "").startswith("actions/upload-artifact")
        )["with"]
        self.assertEqual(
            sorted(upload["path"].split()),
            [
                "public-repo/public/data/recovered-summary.json",
                "public-repo/public/data/source-freshness.json",
                "public-repo/public/index.html",
            ],
        )
        self.assertEqual(upload["retention-days"], 1)
        self.assertEqual(upload["if-no-files-found"], "error")


class OverlayScriptTests(unittest.TestCase):
    """Run the real overlay shell step against hostile artifact layouts."""

    SCRIPT = step("deploy", "Overlay exactly the three refreshed files")["run"]
    FILES = ("data/recovered-summary.json", "data/source-freshness.json", "index.html")

    def run_overlay(self, build):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            refreshed = root / "refreshed"
            (root / "public/data").mkdir(parents=True)
            refreshed.mkdir()
            build(refreshed)
            result = subprocess.run(
                ["bash", "-c", self.SCRIPT],
                cwd=root,
                env={"REFRESHED": str(refreshed), "PATH": "/usr/bin:/bin"},
                capture_output=True,
                text=True,
                check=False,
            )
            copied = {
                name: (root / "public" / name).read_text()
                for name in self.FILES
                if (root / "public" / name).exists()
            }
            return result.returncode, copied

    def exact(self, refreshed):
        for name in self.FILES:
            target = refreshed / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("new " + name)

    def test_exact_three_files_are_copied(self):
        code, copied = self.run_overlay(self.exact)
        self.assertEqual(code, 0)
        self.assertEqual(copied, {n: "new " + n for n in self.FILES})

    def test_extra_missing_and_symlinked_content_is_rejected_and_nothing_copied(self):
        def extra(refreshed):
            self.exact(refreshed)
            (refreshed / "data/plates.csv").write_text("x")

        def missing(refreshed):
            self.exact(refreshed)
            (refreshed / "index.html").unlink()

        def symlink(refreshed):
            self.exact(refreshed)
            (refreshed / "index.html").unlink()
            (refreshed / "index.html").symlink_to("/etc/hostname")

        def nested_symlink_dir(refreshed):
            self.exact(refreshed)
            (refreshed / "link").symlink_to(".", target_is_directory=True)

        for build in (extra, missing, symlink, nested_symlink_dir):
            with self.subTest(case=build.__name__):
                code, copied = self.run_overlay(build)
                self.assertNotEqual(code, 0)
                self.assertEqual(copied, {})
