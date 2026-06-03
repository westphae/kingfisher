#!/usr/bin/env bash
# check-wire.sh — fail if the committed Go<->Rust wire fixtures are stale.
#
# The pod wire contract is defined once in pod_wire/src/lib.rs (Rust) and
# mirrored in internal/pod/wire/ (Go). The Go test in internal/pod/wire/
# decodes a checked-in fixtures.txt produced by the Rust side. If lib.rs
# changes without regenerating fixtures.txt, the two languages silently
# diverge. CLAUDE.md documents the regen step as honour-system; this script
# enforces it so CI / a pre-commit hook can catch drift.
#
# Usage:
#   scripts/check-wire.sh          # verify fixtures match the current crate
#   scripts/check-wire.sh --write  # regenerate fixtures.txt in place
#
# Exit codes: 0 = in sync (or written), 1 = drift detected, 2 = setup error.

set -euo pipefail

# Resolve repo root from this script's location so cwd doesn't matter.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
fixtures="${repo_root}/internal/pod/wire/testdata/fixtures.txt"

# cargo may not be on PATH in a non-login shell (CI, hooks).
if ! command -v cargo >/dev/null 2>&1; then
  # shellcheck disable=SC1090
  [ -f "${HOME}/.cargo/env" ] && source "${HOME}/.cargo/env"
fi
if ! command -v cargo >/dev/null 2>&1; then
  echo "check-wire: cargo not found (source ~/.cargo/env or install Rust)" >&2
  exit 2
fi

generate() {
  ( cd "${repo_root}/pod_wire" && cargo run --quiet --example pod_wire_dump )
}

if [ "${1:-}" = "--write" ]; then
  generate > "${fixtures}"
  echo "check-wire: regenerated ${fixtures#"${repo_root}/"}"
  exit 0
fi

if [ ! -f "${fixtures}" ]; then
  echo "check-wire: missing ${fixtures}" >&2
  exit 2
fi

tmp="$(mktemp)"
trap 'rm -f "${tmp}"' EXIT
generate > "${tmp}"

if diff -u "${fixtures}" "${tmp}"; then
  echo "check-wire: fixtures in sync with pod_wire/src/lib.rs"
  exit 0
fi

echo "" >&2
echo "check-wire: FIXTURES ARE STALE — pod_wire changed without regenerating." >&2
echo "  Run: scripts/check-wire.sh --write && go test ./internal/pod/wire/" >&2
exit 1
