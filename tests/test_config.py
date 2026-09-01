import contextlib
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from syncbridge.cli import main
from syncbridge.config import init_env, load_env


class ConfigTests(unittest.TestCase):
    def test_init_creates_distinct_secrets_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / ".env"
            self.assertTrue(init_env(str(file)))
            original = file.read_bytes()
            with patch.dict(os.environ, {}, clear=True):
                load_env(str(file))
                token = os.environ["SYNCBRIDGE_API_TOKEN"]
                secret = os.environ["SYNCBRIDGE_WEBHOOK_SECRET"]
                self.assertEqual(len(token), 64)
                self.assertEqual(len(secret), 64)
                self.assertNotEqual(token, secret)
            self.assertFalse(init_env(str(file)))
            self.assertEqual(file.read_bytes(), original)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)

    def test_literal_values_and_process_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / ".env"
            file.write_text('A="text # value=1"\nB=${A}\nC=$(never-run)\nKEEP=file\nEMPTY=file\n', encoding="utf-8")
            with patch.dict(os.environ, {"KEEP": "process", "EMPTY": ""}, clear=True):
                load_env(str(file))
                self.assertEqual(os.environ["A"], "text # value=1")
                self.assertEqual(os.environ["B"], "${A}")
                self.assertEqual(os.environ["C"], "$(never-run)")
                self.assertEqual(os.environ["KEEP"], "process")
                self.assertEqual(os.environ["EMPTY"], "")

    def test_invalid_configuration_is_not_partially_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / ".env"
            for invalid in ("bad assignment", "A=duplicate", 'B="unterminated', "B=secret\x00"):
                file.write_text("A=secret-value\n" + invalid, encoding="utf-8")
                with patch.dict(os.environ, {}, clear=True):
                    with self.assertRaises(ValueError) as error:
                        load_env(str(file))
                    self.assertNotIn("A", os.environ)
                    self.assertNotIn("secret", str(error.exception))

    def test_cli_loads_configuration_before_serve(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / ".env"
            file.write_text("SYNCBRIDGE_API_TOKEN=file-token\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), patch("sys.argv", ["syncbridge", "--env-file", str(file), "serve"]), patch("syncbridge.cli.serve") as serve:
                main()
                serve.assert_called_once_with("0.0.0.0", 8080)
                self.assertEqual(os.environ["SYNCBRIDGE_API_TOKEN"], "file-token")

    def test_cli_init_output_never_contains_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / ".env"
            output = io.StringIO()
            with patch("sys.argv", ["syncbridge", "--env-file", str(file), "init"]), contextlib.redirect_stdout(output):
                main()
            with patch.dict(os.environ, {}, clear=True):
                load_env(str(file))
                self.assertNotIn(os.environ["SYNCBRIDGE_API_TOKEN"], output.getvalue())
                self.assertNotIn(os.environ["SYNCBRIDGE_WEBHOOK_SECRET"], output.getvalue())
