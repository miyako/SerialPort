import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_provenance import source_digest


class SourceDigestTests(unittest.TestCase):
    def test_digest_uses_case_sensitive_posix_order_on_every_platform(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            inputs = {
                "template.json": b'{"schema":1}\n',
                "package.json": b'{"name":"SerialPort"}\n',
                "cli/CMakeLists.txt": b"project(serial-port)\n",
                "cli/PROTOCOL.md": b"# Protocol\n",
                "cli/main.cpp": b"int main() {}\n",
            }
            expected = hashlib.sha256()
            for name, content in inputs.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                expected.update(name.encode("utf-8") + b"\0" + content + b"\0")
            self.assertEqual(source_digest(root), expected.hexdigest())
            with patch.object(type(root), "__lt__",
                              lambda left, right: left.as_posix().casefold() < right.as_posix().casefold()):
                self.assertEqual(source_digest(root), expected.hexdigest())
