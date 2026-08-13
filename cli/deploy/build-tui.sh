#!/bin/bash
# Build and prepare the checkout-local maxbridge-tui entry point.

set -euo pipefail

CLI_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CLI_ROOT"

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi

mkdir -p dist
rm -f dist/maxbridge-*.whl
.venv/bin/python -m pip wheel --no-deps --wheel-dir dist .

WHEELS=(dist/maxbridge-*.whl)
if [ "${#WHEELS[@]}" -ne 1 ] || [ ! -f "${WHEELS[0]}" ]; then
    echo "Expected exactly one maxbridge wheel in $CLI_ROOT/dist" >&2
    exit 1
fi

WHEEL_PATH="$CLI_ROOT/${WHEELS[0]}"
.venv/bin/python -m pip install --force-reinstall --no-deps "$WHEEL_PATH"

TUI_PATH="$CLI_ROOT/.venv/bin/maxbridge-tui"
if [ ! -x "$TUI_PATH" ]; then
    echo "maxbridge-tui was not installed at $TUI_PATH" >&2
    exit 1
fi

echo "Wheel: $WHEEL_PATH"
echo "TUI:   $TUI_PATH"
