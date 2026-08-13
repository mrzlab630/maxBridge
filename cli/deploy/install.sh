#!/bin/bash
# Build and install maxBridge into this checkout.

set -euo pipefail

CLI_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CLI_ROOT"

echo "=== maxBridge local installation ==="

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi

.venv/bin/python -m pip install .
if [ "${INSTALL_DEV:-0}" = "1" ]; then
    .venv/bin/python -m pip install '.[dev]'
fi

mkdir -p config data logs
if [ ! -f config/local.yaml ]; then
    cp src/maxbridge/data/default.yaml config/local.yaml
    chmod 600 config/local.yaml 2>/dev/null || true
    echo "Config created: $CLI_ROOT/config/local.yaml"
else
    echo "Config preserved: $CLI_ROOT/config/local.yaml"
fi

echo ""
echo "=== Installation complete ==="
echo "cd $CLI_ROOT"
echo "Edit config:  \$EDITOR config/local.yaml"
echo "Authenticate: .venv/bin/maxbridge --auth-only -c config/local.yaml"
echo "Start:        .venv/bin/maxbridge -c config/local.yaml"
