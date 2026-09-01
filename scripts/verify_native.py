"""Run installed CLI commands outside the source checkout, with disposable data."""
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path


def verify():
    executable = shutil.which("syncbridge")
    if not executable:
        raise RuntimeError("install the package before running native verification")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("SYNCBRIDGE_") and key != "DATABASE_URL"}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)

        def run(*args):
            return subprocess.run([executable, *args], cwd=root, env=env, check=True,
                                  text=True, capture_output=True).stdout

        run("init")
        original = (root / ".env").read_bytes()
        run("init")
        assert (root / ".env").read_bytes() == original
        with (root / ".env").open("a", encoding="utf-8") as handle:
            handle.write("SYNCBRIDGE_DB=native-check.db\n")
        (root / "input.csv").write_text("id,name\n1,native-test-fixture\n", encoding="utf-8")
        assert json.loads(run("import-csv", "input.csv")) == {"created": 1, "duplicates": 0}
        assert json.loads(run("import-csv", "input.csv")) == {"created": 0, "duplicates": 1}
        db = sqlite3.connect(root / "native-check.db")
        try:
            assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        finally:
            db.close()
        print("PASS: installed CLI init, existing configuration preserved, .env loaded, persistent CSV deduplication")


if __name__ == "__main__":
    verify()
