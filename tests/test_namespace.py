"""The public namespace stays aligned across configuration and host-facing surfaces."""
import json
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "component/SerialPort"


class NamespaceTests(unittest.TestCase):
    def test_namespace_configuration_and_component_identity(self):
        config = json.loads((ROOT / "template.json").read_text(encoding="utf-8"))
        self.assertEqual(config["namespace"], "Serial")
        self.assertEqual(config["repository"], "SerialPort")
        self.assertEqual(config["feature_class"], "SerialPort")
        self.assertEqual(config["cli"], "serial-port")
        settings = ET.parse(COMPONENT / "Project/Sources/settings.4DSettings")
        self.assertEqual(settings.find(".//general").get("component_classStore_name"), "Serial")

    def test_generated_completion_metadata_exports_the_same_namespace(self):
        syntax = json.loads((COMPONENT / "Resources/en.lproj/syntaxEN.json").read_text(encoding="utf-8"))
        self.assertEqual([name for name in syntax if name.startswith("cs.")], ["cs.Serial"])
        self.assertIn("SerialPort", syntax["cs.Serial"])

    def test_host_methods_and_examples_use_the_public_namespace(self):
        methods = ROOT / "tests/host/Project/Sources/Methods"
        paths = [*methods.glob("*.4dm"), *(ROOT / "examples").glob("*.4dm")]
        references = 0
        for path in paths:
            namespaces = re.findall(r"\bcs\.([A-Za-z]\w*)\.", path.read_text(encoding="utf-8"))
            references += len(namespaces)
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertTrue(all(name == "Serial" for name in namespaces))
        self.assertGreater(references, 0)

    def test_host_facing_documentation_uses_the_same_namespace(self):
        paths = [ROOT / "README.md", *COMPONENT.glob("Documentation/Classes/*.md")]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            namespaces = re.findall(r"\bcs\.([A-Za-z]\w*)\.", text)
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertTrue(all(name == "Serial" for name in namespaces))


if __name__ == "__main__":
    unittest.main()
