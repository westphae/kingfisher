#!/bin/sh
# Fail if a private key or an ed25519 public-key blob is tracked.
# The needles are assembled here so this script is not itself a match.
set -eu

root=$(git rev-parse --show-toplevel)
cd "$root"

priv=$(printf '%s%s' "BEGIN OPENSSH PRIVATE " "KEY")
pub=$(printf '%s%s' "ssh-ed25519 " "AAAA")
status=0

scan() {
	needle=$1
	label=$2
	files=$(git grep -F -I -l -e "$needle" -- . 2>/dev/null || true)
	if [ -n "$files" ]; then
		echo "check-secrets: tracked file contains ${label}:" >&2
		printf '%s\n' "$files" >&2
		status=1
	fi
}

scan "$priv" "an OpenSSH private key"
scan "$pub" "an ed25519 public key"

if [ "$status" -ne 0 ]; then
	echo "check-secrets: refusing. Keys stay on the Pi, named by path in ~/.config/kingfisher/jpi.env." >&2
	exit 1
fi
echo "check-secrets: no tracked key material"
