"""Check discoverable entry points and local links without installing a skill."""
import json
from pathlib import Path
import re
import unittest
import urllib.parse


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".github/skills/4d-cli-component/SKILL.md"


class PlaybookTests(unittest.TestCase):
    def test_skill_has_descriptive_discoverable_frontmatter(self):
        text = SKILL.read_text(encoding="utf-8")
        match = re.match(r"\A---\n(.*?)\n---\n", text, flags=re.DOTALL)
        self.assertIsNotNone(match, "SKILL.md needs YAML frontmatter")
        frontmatter = match[1]
        self.assertRegex(frontmatter, r"(?m)^name: [a-z0-9-]{1,64}\s*$")
        self.assertRegex(frontmatter, r"(?m)^description:")
        for trigger in ("4D", "Go", "Rust", "C++", "tool4d", "namespace", "initializ"):
            self.assertIn(trigger.lower(), frontmatter.lower())
        self.assertIn("AGENTS.md", text)

    def test_local_documentation_links_resolve_inside_repository(self):
        for source in (ROOT / "AGENTS.md", ROOT / "README.md",
                       ROOT / ".github/copilot-instructions.md", SKILL):
            text = source.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                if target.startswith(("https://", "http://", "#")) or "<" in target:
                    continue
                path = urllib.parse.unquote(target.split("#", 1)[0])
                destination = (source.parent / path).resolve()
                with self.subTest(source=source.relative_to(ROOT), target=target):
                    self.assertTrue(destination.is_relative_to(ROOT))
                    self.assertTrue(destination.exists(), "broken local playbook link")

    def test_versioned_public_pin_and_cross_platform_checkout_policy(self):
        tool = json.loads((ROOT / "package.json").read_text())["tool"]
        self.assertEqual(tool["build"], "101905")
        self.assertEqual(len(tool["sha256"]), 64)
        attributes = (ROOT / ".gitattributes").read_text()
        self.assertIn("* text=auto eol=lf", attributes)
        self.assertIn("*.png binary", attributes)


if __name__ == "__main__":
    unittest.main()
