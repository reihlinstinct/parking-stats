"""Regression for Pages artifact selection on job reruns."""

import re
import unittest
from pathlib import Path


class PagesWorkflowTests(unittest.TestCase):
    def test_attempt_scoped_upload_and_deploy_names_match(self):
        workflow = (
            Path(__file__).parents[1] / ".github/workflows/pages.yml"
        ).read_text()
        workflow = workflow[workflow.index("\n  deploy:") :]
        upload = re.search(
            r"^          name: (github-pages-.+)$", workflow, re.MULTILINE
        )
        deploy = re.search(r"^          artifact_name: (.+)$", workflow, re.MULTILINE)
        self.assertIsNotNone(upload)
        self.assertIsNotNone(deploy)
        assert upload is not None and deploy is not None
        expected = "github-pages-${{ github.run_attempt }}"
        self.assertEqual(upload.group(1), expected)
        self.assertEqual(deploy.group(1), expected)
        self.assertNotEqual(
            expected.replace("${{ github.run_attempt }}", "1"),
            expected.replace("${{ github.run_attempt }}", "2"),
        )

    def test_readiness_uses_upload_output_before_single_deployment(self):
        import yaml

        workflow = yaml.safe_load(
            (Path(__file__).parents[1] / ".github/workflows/pages.yml").read_text()
        )
        steps = workflow["jobs"]["deploy"]["steps"]
        upload = next(s for s in steps if s.get("id") == "pages_upload")
        readiness = next(
            s
            for s in steps
            if s.get("uses") == "./.github/actions/pages-artifact-readiness"
        )
        deploys = [
            s for s in steps if s.get("uses", "").startswith("actions/deploy-pages@")
        ]
        self.assertEqual(len(deploys), 1)
        self.assertLess(steps.index(upload), steps.index(readiness))
        self.assertLess(steps.index(readiness), steps.index(deploys[0]))
        self.assertEqual(
            readiness["with"]["artifact-id"],
            "${{ steps.pages_upload.outputs.artifact_id }}",
        )
        self.assertEqual(readiness["with"]["artifact-name"], upload["with"]["name"])
        self.assertEqual(
            readiness["with"]["artifact-name"], deploys[0]["with"]["artifact_name"]
        )

    def test_readiness_offline_node_regressions(self):
        import subprocess

        subprocess.run(
            ["node", "tests/pages-artifact.test.cjs"],
            cwd=Path(__file__).parents[1],
            check=True,
            capture_output=True,
            text=True,
            # All service waits are mocked. Allow shared-runner process startup
            # headroom, without the CLI test runner's extra isolation child.
            timeout=60,
        )

    def test_action_metadata_false_positive_allowance_is_exact(self):
        import yaml

        from parking_stats.privacy_scan import load_allowlist, scan_lines

        root = Path(__file__).parents[1]
        path = ".github/actions/pages-artifact-readiness/action.yml"
        metadata = yaml.safe_load((root / path).read_text())
        import hashlib

        # Lock the entire static metadata, including all schema keys and text.
        self.assertEqual(
            hashlib.sha256((root / path).read_bytes()).hexdigest(),
            "fc854d24feba8f5d1499b03ce6de79f8f0c653269b2849113982f7b25c21ea85",
        )
        self.assertEqual(set(metadata["inputs"]), {"artifact-id", "artifact-name"})
        self.assertEqual(metadata["runs"], {"using": "node24", "main": "index.cjs"})
        allow = load_allowlist(root)
        self.assertEqual(allow[path], {"raw-export-key"})
        # The allowance does not disable phone detection in this metadata.
        synthetic_phone = "0" + "54" + "123" + "4567"
        findings = scan_lines(path, [synthetic_phone], allow[path])
        self.assertTrue(any(f.rule == "phone" for f in findings))
        self.assertNotIn(".github/actions/pages-artifact-readiness/index.cjs", allow)
