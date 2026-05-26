#!/usr/bin/env bash
set -euo pipefail
echo "Adding ulimit overrides for chayuan service..."
cat <<'EOF' | sudo tee /etc/security/limits.d/99-chayuan.conf
*  soft  nofile  65536
*  hard  nofile  65536
EOF
echo "Re-login (or restart your shell) for the new limits to take effect."
