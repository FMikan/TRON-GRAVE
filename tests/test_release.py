import unittest
from pathlib import Path

from _version import __version__

REPO = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def test_the_changelog_covers_the_current_version_and_the_unreleased_fixes(self):
        text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## {__version__}", text)
        self.assertIn("## Unreleased", text)
        for n in range(1, 26):
            self.assertIn(f"QA-{n:02d}", text)

    def test_ci_runs_the_suite_on_every_os_and_builds_the_exe(self):
        workflow = (REPO / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        for needle in ("ubuntu-latest", "windows-latest", "macos-latest", '"3.10"', "xvfb-run -a",
                       "python -m unittest discover -s tests -t . -v", "TRON-GRAVE.spec",
                       "actions/upload-artifact"):
            self.assertIn(needle, workflow)
