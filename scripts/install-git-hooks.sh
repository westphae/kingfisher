#!/usr/bin/env bash
# Point this clone at the tracked hooks in .githooks.
# Does not change global git config and does not touch other clones.

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "${repo_root}"

git config core.hooksPath .githooks

echo "git config core.hooksPath=.githooks (this clone only)"
echo "pre-commit runs scripts/lint.sh"
