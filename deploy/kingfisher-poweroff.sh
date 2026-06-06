#!/bin/sh
# Sync filesystems and power off the Pi. Invoked by kingfisher after a
# graceful shutdown (buffer flush, WAL checkpoint, DB close).
#
# Install:
#   sudo install -m 755 deploy/kingfisher-poweroff.sh /usr/local/bin/
# Sudoers (see deploy/poweroff/verify.md):
#   eric ALL=(root) NOPASSWD: /usr/local/bin/kingfisher-poweroff.sh
set -e

sync
sync
/usr/bin/systemctl poweroff
