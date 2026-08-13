#!/bin/bash
# Build and prepare the checkout-local maxbridge-tui entry point.

set -euo pipefail

CLI_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CLI_ROOT"

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi

mkdir -p config data logs
CONFIG_PATH="$CLI_ROOT/config/local.yaml"
CONFIG_TMP=""
cleanup_config_tmp() {
    if [ -n "$CONFIG_TMP" ]; then
        rm -f -- "$CONFIG_TMP"
    fi
}
trap cleanup_config_tmp EXIT

if [ -e "$CONFIG_PATH" ] || [ -L "$CONFIG_PATH" ]; then
    echo "Config preserved: $CONFIG_PATH"
else
    PREVIOUS_UMASK="$(umask)"
    umask 077
    CONFIG_TMP="$(mktemp "$CLI_ROOT/config/.local.yaml.tmp.XXXXXX")"
    if ! cp -- src/maxbridge/data/default.yaml "$CONFIG_TMP"; then
        echo "Failed to copy default config to $CONFIG_TMP" >&2
        exit 1
    fi
    if ! chmod 600 -- "$CONFIG_TMP"; then
        echo "Failed to set mode 0600 on $CONFIG_TMP" >&2
        exit 1
    fi
    umask "$PREVIOUS_UMASK"

    if LN_ERROR="$(ln -- "$CONFIG_TMP" "$CONFIG_PATH" 2>&1)"; then
        rm -f -- "$CONFIG_TMP"
        CONFIG_TMP=""
        echo "Config created: $CONFIG_PATH"
    elif [ -e "$CONFIG_PATH" ] || [ -L "$CONFIG_PATH" ]; then
        echo "Config preserved: $CONFIG_PATH"
    else
        echo "Failed to publish config at $CONFIG_PATH: $LN_ERROR" >&2
        exit 1
    fi
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
printf 'Start: (cd %q && exec .venv/bin/maxbridge-tui)\n' "$CLI_ROOT"
