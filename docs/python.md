# Python tooling (uv)

Kingfisher’s Python side projects use **[uv](https://docs.astral.sh/uv/)** as a
workspace. From the repo root:

| Path | Purpose |
|------|---------|
| `pyproject.toml` | Workspace root (`members = analysis, enclosures`) |
| `uv.lock` | Committed lockfile for both members |
| `.venv/` | Shared workspace venv (gitignored) |
| `analysis/pyproject.toml` | Flight DB catalog / health (`numpy`, `matplotlib`) |
| `enclosures/pyproject.toml` | CadQuery case models |
| `hardware/hat/scripts/` | Stdlib only — **no** uv project; KiCad/`pcbnew` = apt |

```bash
# Install uv once (also in deploy/system-rebuild-playbook.md)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync workspace (analysis + enclosures) into ./.venv
uv sync --all-packages

# Analysis CLI
uv run --project analysis python scripts/analyze_flights.py all

# Enclosure regen (cwd = case/ so STEP/STL land beside the script)
cd enclosures/case && uv run --project .. python pi5_aviation_case.py
```

Do **not** put KiCad’s `pcbnew` module in the uv venv — use Debian/RPi
`python3-pcbnew` for any PCB API scripts.
