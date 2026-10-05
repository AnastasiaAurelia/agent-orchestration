#!/usr/bin/env bash
# Governed-runtime distribution manager.
#
# Separate from ./install.sh, which installs only the portable project-facing
# Layer 2 integration.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/diana/product/runtime_install.py" "$@"
