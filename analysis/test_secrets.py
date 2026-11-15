"""Tracked files must not contain SSH key material, and the NAS pull must
stay a read-only rsync of U*.JPI.

The NAS key lives at ~/.ssh/nas_ro on the Pi and is named by path from
~/.config/kingfisher/jpi.env. This test fails if a private key banner or
an ed25519 public blob is in the git index's tree.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check-secrets.sh"

_PRIV = "BEGIN OPENSSH PRIVATE " + "KEY"
_PUB = "ssh-ed25519 " + "AAAA"


def _run(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT)],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


class SecretScanTests(unittest.TestCase):
    def test_this_checkout_has_no_tracked_keys(self):
        proc = _run(REPO)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("no tracked key material", proc.stdout)

    def test_private_key_banner_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.check_call(["git", "init", "-q"], cwd=root)
            (root / "leak.txt").write_text(f"-----{_PRIV}-----\n", encoding="utf-8")
            subprocess.check_call(["git", "add", "leak.txt"], cwd=root)
            proc = _run(root)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("leak.txt", proc.stderr)

    def test_ed25519_public_blob_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.check_call(["git", "init", "-q"], cwd=root)
            (root / "leak.pub").write_text(f"{_PUB}not-a-real-blob comment\n", encoding="utf-8")
            subprocess.check_call(["git", "add", "leak.pub"], cwd=root)
            proc = _run(root)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("leak.pub", proc.stderr)


class NasPullTests(unittest.TestCase):
    def test_rsync_uses_the_restricted_key_and_does_not_fetch_flights(self):
        script = REPO / "deploy" / "jpi" / "kingfisher-jpi-ingest.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bindir = root / "bin"
            bindir.mkdir()
            log = root / "rsync.args"
            flights = root / "flights"
            flights.mkdir()
            repo = root / "repo"
            repo.mkdir()
            key = root / "nas_ro"
            key.write_text("path-only-placeholder\n", encoding="utf-8")
            stubs = {
                "ip": "#!/bin/sh\necho '    inet 192.168.86.151/24 brd 192.168.86.255 scope global wlan1'\n",
                "nc": "#!/bin/sh\nexit 0\n",
                "rsync": "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$RSYNC_LOG\"\nexit 0\n",
                "uv": "#!/bin/sh\nexit 0\n",
            }
            for name, body in stubs.items():
                path = bindir / name
                path.write_text(body, encoding="utf-8")
                path.chmod(path.stat().st_mode | stat.S_IEXEC)
            env_file = root / "jpi.env"
            env_file.write_text(
                "\n".join(
                    [
                        'TRANSPORT="ssh"',
                        'REQUIRE_IFACE="wlan1"',
                        'NAS_HOST="192.168.86.40"',
                        'SSH_USER="eric"',
                        'SSH_PORT="22"',
                        f'SSH_KEY="{key}"',
                        'NAS_PATH=""',
                        f'RAW_DIR="{root / "raw"}"',
                        f'FLIGHTS_DIR="{flights}"',
                        f'LEDGER="{root / "ledger.json"}"',
                        f'REPO="{repo}"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
            env["HOME"] = str(root)
            env["KINGFISHER_JPI_ENV"] = str(env_file)
            env["KINGFISHER_JPI_CONF"] = str(root / "no-such.conf")
            env["RSYNC_LOG"] = str(log)
            env["UV"] = str(bindir / "uv")
            proc = subprocess.run(
                ["sh", str(script)],
                cwd=root,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            args = log.read_text(encoding="utf-8").splitlines()
            self.assertIn("--include=U*.JPI", args)
            self.assertIn("--exclude=*", args)
            self.assertNotIn("--delete", args)
            self.assertIn("eric@192.168.86.40:", args)
            self.assertNotIn(str(flights), args)
            ssh = next(arg for arg in args if arg.startswith("ssh "))
            self.assertIn(f"-i {key}", ssh)
            self.assertIn("IdentitiesOnly=yes", ssh)
            self.assertIn("BatchMode=yes", ssh)
            self.assertNotIn("StrictHostKeyChecking=accept-new", ssh)


if __name__ == "__main__":
    unittest.main()
