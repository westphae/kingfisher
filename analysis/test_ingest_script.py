"""The ingest script's argv has to be something analyze_flights.py accepts.

`--flights-dir` is a top-level option. The copy installed at
`/usr/local/bin` used to pass it after `jpi-auto` and `engine`, and
argparse exited with "unrecognized arguments" on every run.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "deploy" / "jpi" / "kingfisher-jpi-ingest.sh"


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "analyze_flights", REPO / "scripts" / "analyze_flights.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_script(root: Path) -> list[list[str]]:
    bindir = root / "bin"
    bindir.mkdir()
    log = root / "uv.args"
    flights = root / "flights"
    flights.mkdir()
    repo = root / "repo"
    repo.mkdir()
    (repo / "scripts").mkdir()
    stubs = {
        "ip": "#!/bin/sh\necho '    inet 192.168.86.151/24 brd 192.168.86.255 scope global wlan1'\n",
        "nc": "#!/bin/sh\nexit 0\n",
        "rsync": "#!/bin/sh\nexit 0\n",
        "uv": "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$UV_LOG\"\nexit 0\n",
    }
    for name, body in stubs.items():
        path = bindir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    env_file = root / "jpi.env"
    env_file.write_text(
        "\n".join(
            [
                'TRANSPORT="none"',
                f'FLIGHTS_DIR="{flights}"',
                f'RAW_DIR="{root / "raw"}"',
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
    env["UV_LOG"] = str(log)
    env.pop("UV", None)
    proc = subprocess.run(
        ["sh", str(SCRIPT)],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stdout + proc.stderr)
    lines = [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [line.split() for line in lines]


class IngestArgvTests(unittest.TestCase):
    def test_script_argv_parses_before_and_after_the_subcommand(self):
        cli = _load_cli()
        parser = cli.build_parser()
        with tempfile.TemporaryDirectory() as tmp:
            invocations = _run_script(Path(tmp))
        self.assertEqual(len(invocations), 2, invocations)
        commands = []
        for argv in invocations:
            # uv run --project analysis python scripts/analyze_flights.py ...
            py = argv.index("scripts/analyze_flights.py")
            tail = argv[py + 1 :]
            args = parser.parse_args(tail)
            commands.append(args.cmd)
            self.assertTrue(str(args.flights_dir).endswith("flights"), args)
            # The previously installed script passed the same option after
            # the subcommand. That order has to keep parsing.
            moved = _flights_dir_after_subcommand(tail)
            again = parser.parse_args(moved)
            self.assertEqual(again.cmd, args.cmd)
            self.assertEqual(again.flights_dir, args.flights_dir)
        self.assertEqual(commands, ["jpi-auto", "engine"])
        self.assertEqual(invocations[0][invocations[0].index("scripts/analyze_flights.py") + 1], "--flights-dir")
        self.assertEqual(invocations[1][invocations[1].index("scripts/analyze_flights.py") + 1], "--flights-dir")


def _flights_dir_after_subcommand(tail: list[str]) -> list[str]:
    out = list(tail)
    i = out.index("--flights-dir")
    flag, value = out[i], out[i + 1]
    del out[i : i + 2]
    cmd = out.index("jpi-auto") if "jpi-auto" in out else out.index("engine")
    out[cmd + 1 : cmd + 1] = [flag, value]
    return out


if __name__ == "__main__":
    unittest.main()
