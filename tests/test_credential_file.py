"""Credential file reader and its use from the service config (card H1.4)."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from villa_kuppis.runtime import Config, run
from villa_kuppis.credential_file import CredentialError, read_credentials_file

POSIX = unittest.skipIf(os.name == "nt", "POSIX file modes")


def write_secret(folder, data, mode=0o600):
    path = Path(folder) / "melcloud"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    os.chmod(path, mode)
    return path


class CredentialFileTests(unittest.TestCase):
    def test_reads_username_and_password(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_secret(temp, {"username": "a@example.invalid", "password": "pw"})
            found = read_credentials_file(path)
            self.assertEqual((found.username, found.password), ("a@example.invalid", "pw"))
            self.assertNotIn("pw", repr(found))

    @POSIX
    def test_group_or_world_access_is_refused_without_leaking_the_secret(self):
        with tempfile.TemporaryDirectory() as temp:
            for mode in (0o640, 0o604, 0o660):
                path = write_secret(temp, {"username": "u", "password": "hunter2-secret"}, mode)
                with self.assertRaises(CredentialError) as caught:
                    read_credentials_file(path)
                self.assertNotIn("hunter2-secret", str(caught.exception))

    def test_bad_files_give_errors_that_never_echo_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            bad = ["not json hunter2-secret", json.dumps(["hunter2-secret"]),
                   json.dumps({"username": "u", "password": ""}),
                   json.dumps({"username": "u", "password": "hunter2-secret", "extra": 1}),
                   json.dumps({"username": 5, "password": "hunter2-secret"})]
            for text in bad:
                with self.assertRaises(CredentialError) as caught:
                    read_credentials_file(write_secret(temp, text))
                self.assertNotIn("hunter2-secret", str(caught.exception))
            with self.assertRaises(CredentialError):
                read_credentials_file(Path(temp) / "missing")
            with self.assertRaises(CredentialError):
                read_credentials_file("relative/melcloud")


class ConfigCredentialFileTests(unittest.TestCase):
    def config_file(self, temp, **extra):
        values = {"data_dir": str(Path(temp) / "data"),
                  "backup_dir": str(Path(temp) / "backups"), **extra}
        path = Path(temp) / "service.json"
        path.write_text(json.dumps(values))
        return path

    def test_credential_file_is_optional_and_must_be_absolute(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(Config.load(self.config_file(temp)).credential_file)
            secret = str(Path(temp) / "melcloud")
            self.assertEqual(Config.load(self.config_file(temp, credential_file=secret)
                                         ).credential_file, Path(secret))
            for bad in ("relative", 5):
                with self.assertRaises(ValueError):
                    Config.load(self.config_file(temp, credential_file=bad))

    def run_live(self, config):
        captured = {}
        def make(data, **kwargs):
            captured.update(kwargs)
            return mock.Mock()
        stop = mock.Mock()
        stop.is_set.return_value = True
        with mock.patch("villa_kuppis.runtime.make_live_service", side_effect=make):
            server = mock.Mock()
            run(config, stop_event=stop, server_factory=lambda *a, **k: server,
                backup_factory=lambda *a: mock.Mock(), price_factory=lambda *a: None)
        return captured

    def test_file_supplies_the_melcloud_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            secret = write_secret(temp, {"username": "a@example.invalid", "password": "pw"})
            config = Config.load(self.config_file(temp, credential_file=str(secret)))
            captured = self.run_live(config)
            self.assertEqual(captured["credentials"].username, "a@example.invalid")

    def test_live_melcloud_without_file_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Config.load(self.config_file(temp))
            with self.assertRaisesRegex(ValueError, "credential_file"):
                self.run_live(config)


if __name__ == "__main__":
    unittest.main()
