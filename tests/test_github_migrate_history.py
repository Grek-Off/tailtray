"""Historical migration rejects altered identity/closure before downloading payloads."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("github_migrate_history", ROOT / "scripts/github-migrate-history.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
sys.path.pop(0)


class HistoricalMigrationTests(unittest.TestCase):
    def test_only_exact_two_version_matrix_is_authorized(self):
        paths = migration.allowed_payloads()
        self.assertEqual(len(paths), 14)
        self.assertTrue(all("0.1.4" in p or "0.1.5" in p for p in paths))
        self.assertFalse(any(".." in p for p in paths))

    def test_signature_or_manifest_failure_prevents_any_package_download(self):
        key = (ROOT / "packaging/repository-key.asc").read_bytes()
        def fake_fetch(name, limit):
            if name == "repository-key.asc":
                return key
            if name == "manifest.json":
                return json.dumps({"schema": 1, "versions": ["0.1.4", "0.1.5"],
                                   "files": {"../foreign": {}}}).encode()
            return b"signature"
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(migration, "fetch", side_effect=fake_fetch) as fetch, \
                patch.object(migration.release, "verify_signature") as verify:
            with self.assertRaisesRegex(ValueError, "14-file"):
                migration.restore_public(Path(temporary) / "bad-closure")
            self.assertEqual(fetch.call_count, 5)
            fetch.reset_mock()
            verify.side_effect = ValueError("Signature is not trusted")
            with self.assertRaisesRegex(ValueError, "not trusted"):
                migration.restore_public(Path(temporary) / "bad-signature")
            self.assertEqual(fetch.call_count, 5)
