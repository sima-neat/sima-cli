#!/usr/bin/env bash
set -euo pipefail

WHEEL_PATH="${1:-}"
if [[ -z "$WHEEL_PATH" || ! -f "$WHEEL_PATH" ]]; then
    echo "Usage: $0 /path/to/sima_cli.whl" >&2
    exit 2
fi

is_devkit=false
is_elxr=false
is_elxr_sdk=false
is_palette_sdk=false
is_mpk_sdk=false
is_model_sdk=false
skip_sdk_aliases=false
is_debian=false

if [[ -f /etc/build ]]; then
    build_file="/etc/build"
elif [[ -f /etc/buildinfo ]]; then
    build_file="/etc/buildinfo"
else
    build_file=""
fi

if [[ -n "$build_file" ]]; then
    if grep -qiE "modalix|davinci" "$build_file"; then
        is_devkit=true
    fi
    if grep -qi "DISTRO.*elxr" "$build_file"; then
        is_elxr=true
    fi
    if grep -qi "sima" "$build_file"; then
        skip_sdk_aliases=true
        echo "Detected 'sima' in $build_file - skipping SDK shortcuts."
    fi
fi

if [[ -f /etc/sdk-release ]]; then
    is_palette_sdk=true
    skip_sdk_aliases=true
    echo "Palette SDK detected (/etc/sdk-release exists) - skipping SDK shortcuts."
    if command -v mpk >/dev/null 2>&1; then
        mpk_probe_output="$(mpk --help 2>&1 || true)"
        if ! echo "$mpk_probe_output" | grep -qiE "not available in .*modelsdk|mpk_cli_toolset"; then
            is_mpk_sdk=true
            echo "Detected functional MPK command; treating this as MPK SDK."
        fi
    fi
    if grep -qi "elxr" /etc/sdk-release \
        && [[ "$is_mpk_sdk" != true ]] \
        && [[ -f /opt/bin/simaai-init-build-env ]]; then
        is_elxr_sdk=true
        echo "Detected eLXR SDK in /etc/sdk-release."
    fi
fi

if command -v python3 >/dev/null 2>&1; then
    if python3 - <<'PY' >/dev/null 2>&1
import importlib.util
import sys
sys.exit(0 if importlib.util.find_spec("afe") is not None else 1)
PY
    then
        is_model_sdk=true
        echo "Model SDK detected via AFE module presence check."
        is_elxr_sdk=false
    fi
fi

if [[ -f /etc/os-release ]]; then
    distro_id="$(awk -F= '/^ID=/{print $2}' /etc/os-release | tr -d '"')"
    if [[ "$distro_id" == "debian" || "$distro_id" == "ubuntu" ]]; then
        is_debian=true
    fi
fi

if $is_elxr_sdk && [[ -f /etc/apt/sources.list.d/elxr.list ]]; then
    if grep -q "sw-web\.eng\.sima\.ai" /etc/apt/sources.list.d/elxr.list; then
        sudo sed -i '/sw-web\.eng\.sima\.ai/d' /etc/apt/sources.list.d/elxr.list
        echo "Removed unreachable sw-web.eng.sima.ai apt source from elxr.list"
    fi
fi

if [[ "$is_debian" == true || "$is_elxr" == true ]]; then
    echo "Debian-based or eLXR environment detected, installing system dependencies..."
    if [[ "$is_model_sdk" == true ]]; then
        echo "Model SDK detected, skipping apt dependency install."
    else
        if ! sudo apt-get update -o Acquire::Retries=3; then
            echo "WARNING: apt package index refresh failed. Continuing because sima-cli only requires python3-venv and python3-pip." >&2
            echo "If those packages are not available from existing indexes, the package install step will fail with the real missing dependency." >&2
        fi
        sudo apt-get install -y python3-venv python3-pip
    fi
fi

if $is_devkit; then
    BASE_DIR="/data/sima-cli"
    echo "Detected Modalix/Davinci DevKit, using $BASE_DIR"
    sudo mkdir -p "$BASE_DIR"
    sudo chown "$(whoami)" "$BASE_DIR"
else
    BASE_DIR="$HOME/.sima-cli"
    echo "Non-DevKit platform detected, using $BASE_DIR"
    mkdir -p "$BASE_DIR"
fi

if ! touch "$HOME/.setup_test" 2>/dev/null; then
    echo "ERROR: Cannot write to $HOME. Check permissions:"
    ls -ld "$HOME"
    exit 1
else
    rm -f "$HOME/.setup_test"
fi

VENV_DIR="$BASE_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
else
    echo "Virtual environment already exists at $VENV_DIR (reusing)"
fi

VENV_PY="$VENV_DIR/bin/python3"
if [[ ! -x "$VENV_PY" ]]; then
    VENV_PY="$VENV_DIR/bin/python"
fi
if [[ ! -x "$VENV_PY" ]]; then
    echo "ERROR: No Python interpreter found in virtual environment at $VENV_DIR"
    exit 1
fi

if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    echo "pip not found in venv, bootstrapping with ensurepip..."
    "$VENV_PY" -m ensurepip --upgrade
fi

echo "Installing sima-cli wheel into venv..."
PIP_DISABLE_PIP_VERSION_CHECK=1 "$VENV_PY" -m pip install --no-cache-dir --upgrade pip
PIP_DISABLE_PIP_VERSION_CHECK=1 "$VENV_PY" -m pip install --no-cache-dir --force-reinstall "$WHEEL_PATH"

if $skip_sdk_aliases; then
    ALIAS_NAMES=( sima-cli )
    ALIAS_CMDS=( "$VENV_DIR/bin/sima-cli" )
else
    ALIAS_NAMES=( sima-cli sdk mpk modelsdk yocto elxr )
    ALIAS_CMDS=(
        "$VENV_DIR/bin/sima-cli"
        "sima-cli sdk"
        "sima-cli sdk mpk"
        "sima-cli sdk model"
        "sima-cli sdk yocto"
        "sima-cli sdk elxr"
    )
fi

add_aliases() {
    local rc_file="$1"
    [[ -f "$rc_file" ]] || touch "$rc_file" || return 1
    for i in "${!ALIAS_NAMES[@]}"; do
        local name="${ALIAS_NAMES[$i]}"
        local cmd="${ALIAS_CMDS[$i]}"
        local line="alias $name='$cmd'"
        if ! grep -qxF "$line" "$rc_file" 2>/dev/null; then
            echo "$line" >> "$rc_file"
            echo "Added alias: $name -> $cmd"
        else
            echo "Alias '$name' already exists in $rc_file"
        fi
    done
}

add_venv_path() {
    local rc_file="$1"
    local line="export PATH=\"\$PATH:$VENV_DIR/bin\""
    [[ -f "$rc_file" ]] || touch "$rc_file" || return 1
    if ! grep -qxF "$line" "$rc_file" 2>/dev/null; then
        echo "$line" >> "$rc_file"
        echo "Added PATH update for sima-cli venv in $rc_file"
    fi
}

ensure_bashrc_sourced_from_profile() {
    local rc_file="$1"
    [[ "$(basename "$rc_file")" == ".bash_profile" ]] || return 0
    [[ -f "$rc_file" ]] || touch "$rc_file" || return 1

    if grep -Eq '(^|[[:space:]])(\.|source)[[:space:]]+("?\$HOME"?/|~/)?\.bashrc' "$rc_file" 2>/dev/null; then
        return 0
    fi

    cat >> "$rc_file" <<'EOF'

if [ -f "$HOME/.bashrc" ]; then
    . "$HOME/.bashrc"
fi
EOF
    echo "Added .bashrc source line in $rc_file"
}

add_elxr_sdk_env_source() {
    local rc_file="$1"
    local line="source /opt/bin/simaai-init-build-env modalix"
    [[ -f "$rc_file" ]] || touch "$rc_file" || return 1
    if ! grep -qxF "$line" "$rc_file" 2>/dev/null; then
        echo "$line" >> "$rc_file"
        echo "Added eLXR SDK build env source line in $rc_file"
    fi
}

if [[ "${OSTYPE:-}" == "darwin"* ]]; then
    RC_FILE="$HOME/.zshrc"
else
    if $is_palette_sdk || $is_elxr; then
        RC_FILE="$HOME/.bash_profile"
    elif $is_devkit; then
        RC_FILE="$HOME/.bashrc"
    else
        distro_id="$(awk -F= '/^ID=/{print $2}' /etc/os-release 2>/dev/null | tr -d '"')"
        if [[ "$distro_id" == "debian" ]]; then
            RC_FILE="$HOME/.bash_profile"
        else
            RC_FILE="$HOME/.bashrc"
        fi
    fi
fi

ensure_bashrc_sourced_from_profile "$RC_FILE"
add_venv_path "$RC_FILE"
if $is_elxr_sdk && [[ -f /opt/bin/simaai-init-build-env ]]; then
    add_elxr_sdk_env_source "$RC_FILE"
fi
add_aliases "$RC_FILE"

cat <<EOF

sima-cli successfully installed.

   Virtual env: $VENV_DIR
   Binary:      $VENV_DIR/bin/sima-cli
   Shell config: $RC_FILE

Run in this shell:
   source $RC_FILE

Or restart your terminal.
EOF
