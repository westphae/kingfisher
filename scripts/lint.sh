#!/usr/bin/env bash
# Pre-commit lint.
#
# Hard gates:
#   gofmt on staged *.go files (the index blob, not unstaged edits).
#   scripts/check-wire.sh when pod_wire/ or internal/pod/wire/ is part of
#   this commit, and when those trees have unstaged drift. A partial stage
#   is checked twice: the index (what will be committed) and the worktree
#   (what check-wire.sh reads). Cargo missing fails the hook only when wire
#   files are staged; otherwise it prints one line and continues.
#
# Optional:
#   golangci-lint v2, and only if the binary is already on PATH. Packages
#   that cannot be loaded because a sibling module or PAM headers are
#   missing are skipped. .golangci.yml ignores unchecked Close, Rollback,
#   and deadline setters.

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "${repo_root}"

status=0

note() {
  printf '%s\n' "$*" >&2
}

warn() {
  printf '%s\n' "$*" >&2
}

mark_fail() {
  status=1
}

ensure_cargo() {
  if command -v cargo >/dev/null 2>&1; then
    return 0
  fi
  if [[ -f "${HOME}/.cargo/env" ]]; then
    # shellcheck disable=SC1091
    source "${HOME}/.cargo/env"
  fi
  command -v cargo >/dev/null 2>&1
}

env_gap_text() {
  grep -Eiq \
    'go-iio|goflying|geomag|magkal|security/pam|msteinert/pam|replacement directory|pam_appl' \
    "$1"
}

# --- gofmt -----------------------------------------------------------------

gofmt_staged() {
  local any=0 f src formatted count=0 bad=0
  while IFS= read -r -d '' f; do
    case "${f}" in
      *.go) any=1 ;;
      *) continue ;;
    esac
  done < <(git diff --cached --name-only --diff-filter=ACMR -z)

  if ! command -v gofmt >/dev/null 2>&1; then
    if [[ "${any}" -eq 1 ]]; then
      warn "lint: gofmt not found on PATH; staged Go files were not checked."
      return 1
    fi
    note "lint: gofmt skipped (gofmt not on PATH; no staged Go files)."
    return 0
  fi

  while IFS= read -r -d '' f; do
    case "${f}" in
      *.go) ;;
      *) continue ;;
    esac
    count=$((count + 1))
    src="$(mktemp)"
    formatted="$(mktemp)"
    if ! git show ":${f}" > "${src}"; then
      warn "lint: cannot read staged ${f}"
      bad=1
      rm -f "${src}" "${formatted}"
      continue
    fi
    if ! gofmt "${src}" > "${formatted}"; then
      warn "lint: gofmt failed on staged ${f}"
      bad=1
      rm -f "${src}" "${formatted}"
      continue
    fi
    if ! cmp -s "${src}" "${formatted}"; then
      warn "lint: gofmt would change staged ${f}"
      diff -u --label "a/${f}" --label "b/${f}" "${src}" "${formatted}" >&2 || true
      bad=1
    fi
    rm -f "${src}" "${formatted}"
  done < <(git diff --cached --name-only --diff-filter=ACMR -z)

  if [[ "${bad}" -ne 0 ]]; then
    warn "lint: gofmt staged Go files, then stage the result."
    return 1
  fi
  if [[ "${count}" -eq 0 ]]; then
    note "lint: gofmt ok (no staged Go files)."
  else
    note "lint: gofmt ok (${count} staged Go file(s))."
  fi
}

# --- wire fixtures ---------------------------------------------------------

check_wire_index() {
  local tmp out path
  tmp="$(mktemp -d)"
  out="$(mktemp)"

  while IFS= read -r -d '' path; do
    [[ -z "${path}" ]] && continue
    if ! git checkout-index --prefix="${tmp}/" -- "${path}"; then
      warn "lint: failed to read staged ${path}"
      rm -rf "${tmp}" "${out}"
      return 1
    fi
  done < <(git ls-files -z -- pod_wire internal/pod/wire)

  if [[ ! -f "${tmp}/pod_wire/Cargo.toml" || ! -f "${tmp}/internal/pod/wire/testdata/fixtures.txt" ]]; then
    warn "lint: staged wire tree is missing pod_wire/Cargo.toml or fixtures.txt."
    rm -rf "${tmp}" "${out}"
    return 1
  fi

  if ! (cd "${tmp}/pod_wire" && cargo run --quiet --offline --example pod_wire_dump > "${out}" 2>/dev/null); then
    if ! (cd "${tmp}/pod_wire" && cargo run --quiet --example pod_wire_dump > "${out}"); then
      warn "lint: cargo failed while checking staged wire fixtures."
      rm -rf "${tmp}" "${out}"
      return 1
    fi
  fi
  if ! diff -u "${tmp}/internal/pod/wire/testdata/fixtures.txt" "${out}"; then
    warn "lint: staged wire fixtures do not match pod_wire. Run: scripts/check-wire.sh --write"
    rm -rf "${tmp}" "${out}"
    return 1
  fi
  rm -rf "${tmp}" "${out}"
  note "lint: staged wire fixtures match pod_wire."
}

wire_check() {
  local cached unstaged
  cached="$(git diff --cached --name-only --diff-filter=ACMRD -- pod_wire internal/pod/wire)"
  unstaged="$(git diff --name-only --diff-filter=ACMRD -- pod_wire internal/pod/wire)"

  if ! ensure_cargo; then
    if [[ -n "${cached}" ]]; then
      warn "lint: cargo not found; wire files are staged, so the fixture check cannot run."
      warn "lint: install Rust or source ~/.cargo/env, then re-run scripts/check-wire.sh."
      return 1
    fi
    note "lint: cargo not found; skipping wire fixture check."
    return 0
  fi

  if [[ -z "${cached}" && -z "${unstaged}" ]]; then
    note "lint: wire check skipped (pod_wire and internal/pod/wire unchanged)."
    return 0
  fi

  local failed=0
  local worktree_match=0
  if git diff --quiet -- pod_wire internal/pod/wire; then
    worktree_match=1
  fi

  if [[ -n "${cached}" && "${worktree_match}" -eq 0 ]]; then
    note "lint: wire trees differ from the index; checking the staged snapshot and the worktree."
    if ! check_wire_index; then
      failed=1
    fi
  fi

  # Worktree check covers unstaged drift and the common case where the
  # index already matches the files check-wire.sh will read.
  if [[ -n "${unstaged}" || "${worktree_match}" -eq 1 ]]; then
    if ! scripts/check-wire.sh; then
      failed=1
    fi
  fi

  return "${failed}"
}

# --- optional golangci-lint ------------------------------------------------

staged_go_patch() {
  local dest="$1" full
  full="$(mktemp)"
  git diff --cached --diff-filter=ACMR > "${full}" || true
  awk '
    /^diff --git / {
      keep = ($0 ~ /\.go( |$)/)
    }
    keep { print }
  ' "${full}" > "${dest}"
  rm -f "${full}"
}

golangci_staged() {
  if ! command -v golangci-lint >/dev/null 2>&1; then
    note "lint: golangci-lint not on PATH; skipping."
    return 0
  fi

  local ver
  ver="$(golangci-lint version 2>/dev/null || true)"
  if [[ ! "${ver}" =~ version\ 2\. ]]; then
    note "lint: skipping golangci-lint (need v2 to read .golangci.yml; found: ${ver:-unknown})."
    return 0
  fi

  local -a pkgs=()
  local f dir pkg
  declare -A seen=()
  while IFS= read -r -d '' f; do
    case "${f}" in
      *.go) ;;
      *) continue ;;
    esac
    dir="$(dirname "${f}")"
    pkg="./${dir}"
    if [[ -n "${seen[${pkg}]+x}" ]]; then
      continue
    fi
    seen["${pkg}"]=1
    pkgs+=("${pkg}")
  done < <(git diff --cached --name-only --diff-filter=ACMR -z)

  if [[ "${#pkgs[@]}" -eq 0 ]]; then
    note "lint: golangci-lint skipped (no staged Go files)."
    return 0
  fi

  local -a lint_pkgs=()
  local errfile
  errfile="$(mktemp)"
  for pkg in "${pkgs[@]}"; do
    if go list "${pkg}" >/dev/null 2>"${errfile}"; then
      lint_pkgs+=("${pkg}")
      continue
    fi
    if env_gap_text "${errfile}"; then
      note "lint: skipping golangci-lint ${pkg} (sibling module or PAM headers unavailable)."
      continue
    fi
    warn "lint: go list failed for ${pkg}:"
    cat "${errfile}" >&2
    mark_fail
  done
  rm -f "${errfile}"

  if [[ "${#lint_pkgs[@]}" -eq 0 ]]; then
    if [[ "${status}" -eq 0 ]]; then
      note "lint: golangci-lint skipped; staged Go packages need sibling modules or PAM headers."
    fi
    return 0
  fi

  local patch help extra=()
  patch="$(mktemp)"
  staged_go_patch "${patch}"
  help="$(golangci-lint run -h 2>&1 || true)"
  if [[ -s "${patch}" ]] && grep -q -- '--new-from-patch' <<< "${help}"; then
    extra+=(--new-from-patch "${patch}")
  elif grep -q -- '--new-from-rev' <<< "${help}"; then
    extra+=(--new-from-rev HEAD)
  fi

  local rc
  local -a ok_pkgs=()
  errfile="$(mktemp)"
  for pkg in "${lint_pkgs[@]}"; do
    set +e
    golangci-lint run "${extra[@]}" "${pkg}" > "${errfile}" 2>&1
    rc=$?
    set -e
    if [[ "${rc}" -eq 0 ]]; then
      ok_pkgs+=("${pkg}")
      continue
    fi
    if env_gap_text "${errfile}"; then
      note "lint: skipping golangci-lint ${pkg} (sibling module or PAM headers unavailable)."
      continue
    fi
    warn "lint: golangci-lint ${pkg} failed:"
    cat "${errfile}" >&2
    mark_fail
  done
  rm -f "${patch}" "${errfile}"
  if [[ "${status}" -eq 0 && "${#ok_pkgs[@]}" -gt 0 ]]; then
    note "lint: golangci-lint ok (${ok_pkgs[*]})."
  fi
}

if ! gofmt_staged; then
  mark_fail
fi
if ! wire_check; then
  mark_fail
fi
golangci_staged

exit "${status}"
