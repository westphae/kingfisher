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


def _run_script(root: Path, *, uv_in_env: str | None = None, extra_env: str = "") -> subprocess.CompletedProcess[str]:
    bindir = root / "bin"
    bindir.mkdir(exist_ok=True)
    log = root / "uv.args"
    flights = root / "flights"
    flights.mkdir(exist_ok=True)
    repo = root / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "scripts").mkdir(exist_ok=True)
    stubs = {
        "ip": "#!/bin/sh\necho '    inet 192.168.86.151/24 brd 192.168.86.255 scope global wlan1'\n",
        "nc": "#!/bin/sh\nexit 0\n",
        "rsync": "#!/bin/sh\nexit 0\n",
        "uv": "#!/bin/sh\nprintf '%s %s\\n' \"$0\" \"$*\" >> \"$UV_LOG\"\nexit 0\n",
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
                extra_env,
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
    if uv_in_env is not None:
        env["UV"] = uv_in_env
    return subprocess.run(
        ["sh", str(SCRIPT)],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


class IngestArgvTests(unittest.TestCase):
    def test_script_argv_parses_before_and_after_the_subcommand(self):
        cli = _load_cli()
        parser = cli.build_parser()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = _run_script(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            log = (root / "uv.args").read_text(encoding="utf-8")
        invocations = [line.split() for line in log.splitlines() if line.strip()]
        self.assertEqual(len(invocations), 2, invocations)
        # Unset UV resolves through `command -v uv`, which is the stub on PATH.
        self.assertTrue(invocations[0][0].endswith("/uv"), invocations[0])
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
        engine_argv = invocations[1]
        self.assertIn("--changed", engine_argv)
        self.assertNotIn("--all", engine_argv)


    def test_jpi_env_uv_overrides_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chosen = root / "mise-uv"
            chosen.write_text(
                "#!/bin/sh\nprintf '%s %s\\n' \"$0\" \"$*\" >> \"$UV_LOG\"\nexit 0\n",
                encoding="utf-8",
            )
            chosen.chmod(chosen.stat().st_mode | stat.S_IEXEC)
            # The stub named uv stays first on PATH. jpi.env must win.
            proc = _run_script(root, extra_env=f'UV="{chosen}"')
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            first = (root / "uv.args").read_text(encoding="utf-8").splitlines()[0]
            self.assertTrue(first.startswith(str(chosen) + " "), first)

    def test_missing_uv_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _run_script(root)
            empty = root / "empty-path"
            empty.mkdir()
            env = os.environ.copy()
            env["PATH"] = str(empty)
            env["HOME"] = str(root)
            env["KINGFISHER_JPI_ENV"] = str(root / "jpi.env")
            env["KINGFISHER_JPI_CONF"] = str(root / "no-such.conf")
            env.pop("UV", None)
            proc = subprocess.run(
                ["/bin/sh", str(SCRIPT)],
                cwd=root,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("uv not found", proc.stdout + proc.stderr)


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
