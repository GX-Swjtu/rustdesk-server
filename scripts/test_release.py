import importlib.util
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location("server_release", Path(__file__).with_name("release.py"))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ServerReleaseTests(unittest.TestCase):
    def test_packages_contain_binaries_and_identity_but_no_runtime_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ["LICENSE", "README.md", "docs/native-p2p.md", "docs/account-handshake.md",
                         "docs/environment-variables.md", "docs/fork-release.md"]:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(release.ROOT / name, path)
            (root / "target/release").mkdir(parents=True)
            for name in ["hbbs", "hbbr", "rustdesk-utils"]:
                for extension in ["", ".exe"]:
                    (root / "target/release" / (name + extension)).write_bytes(b"test binary")
            (root / "id_ed25519").write_text("must never ship")
            (root / "db_v2.sqlite3").write_text("must never ship")
            for target in release.TARGETS:
                with patch.object(release, "ROOT", root), patch.object(release, "DIST", root / "out"), \
                        patch.dict(os.environ, {"RELEASE_TARGET": target}), \
                        patch.object(release, "command", side_effect=["a" * 40, "b" * 40]):
                    release.package()
                path = root / "out" / release.filename(target)
                if "windows" in target:
                    with zipfile.ZipFile(path) as archive:
                        names = archive.namelist()
                        source = json.loads(archive.read("SOURCE.json"))
                else:
                    with tarfile.open(path) as archive:
                        names = archive.getnames()
                        source = json.load(archive.extractfile("SOURCE.json"))
                self.assertEqual(len(names), 10)
                self.assertIn("hbbs.exe" if "windows" in target else "hbbs", names)
                self.assertNotIn("id_ed25519", names)
                self.assertNotIn("db_v2.sqlite3", names)
                self.assertEqual(source["commit"], "a" * 40)
                self.assertEqual(source["hbb_common"], "b" * 40)

    def test_published_release_and_wrong_version_are_rejected(self):
        tag = f"server-v{release.VERSION}-gx.1"
        with patch.dict(os.environ, {"RELEASE_TAG": tag}), \
                patch.object(release, "command", side_effect=["a", "a", json.dumps([[{"tag_name": tag, "draft": False}]])]), \
                self.assertRaisesRegex(ValueError, "immutable"):
            release.validate()
        with patch.dict(os.environ, {"RELEASE_TAG": "v0.0.0"}), self.assertRaises(ValueError):
            release.validate()


if __name__ == "__main__":
    unittest.main()
