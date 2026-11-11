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
