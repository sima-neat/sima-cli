#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "usage: $0 /path/to/sima-cli" >&2
  exit 64
fi

SIMA_CLI="$1"
TEMP_PARENT="/tmp"
if [[ -n "${RUNNER_TEMP:-}" && -d "${RUNNER_TEMP}" ]]; then
  TEMP_PARENT="${RUNNER_TEMP}"
fi
TEST_ROOT="$(mktemp -d "${TEMP_PARENT}/cloudex-forwarder-e2e.XXXXXX")"
SHIM_DIR="${TEST_ROOT}/shims"
INSTALL_ROOT="${TEST_ROOT}/root"
mkdir -p "${SHIM_DIR}" "${INSTALL_ROOT}"
trap 'rm -rf "${TEST_ROOT}"' EXIT

# Exercise the production package installer without changing /usr/local on a
# persistent runner. The Kerrigan installer still performs its normal root,
# install, and symlink operations; these shims only redirect their filesystem
# targets into this test's private root.
cat >"${SHIM_DIR}/sudo" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-v" ]]; then
  exit 0
fi
if [[ "${1:-}" == "-n" ]]; then
  shift
fi
if [[ "${1:-}" == "--" ]]; then
  shift
fi
exec "$@"
SH

cat >"${SHIM_DIR}/id" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-u" ]]; then
  printf '0\n'
  exit 0
fi
exec /usr/bin/id "$@"
SH

cat >"${SHIM_DIR}/install" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
args=()
for value in "$@"; do
  if [[ "$value" == /usr/local || "$value" == /usr/local/* ]]; then
    value="${CLOUDEX_TEST_ROOT}${value}"
  fi
  args+=("$value")
done
exec /usr/bin/install "${args[@]}"
SH

cat >"${SHIM_DIR}/ln" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
args=()
for value in "$@"; do
  if [[ "$value" == /usr/local || "$value" == /usr/local/* ]]; then
    value="${CLOUDEX_TEST_ROOT}${value}"
  fi
  args+=("$value")
done
exec /bin/ln "${args[@]}"
SH
chmod 0755 "${SHIM_DIR}/sudo" "${SHIM_DIR}/id" "${SHIM_DIR}/install" "${SHIM_DIR}/ln"

fail_with_command_output() {
  local message="$1"
  local output="$2"
  printf '%s\n' "${message}" >&2
  printf '%s\n' '--- command output ---' >&2
  printf '%s\n' "${output}" >&2
  printf '%s\n' '--- end command output ---' >&2
  exit 1
}

export CLOUDEX_TEST_ROOT="${INSTALL_ROOT}"
export SIMA_CLOUDEX_FORWARDER="${INSTALL_ROOT}/usr/local/bin/kerrigan-p2p-forwarder"
export PATH="${SHIM_DIR}:${PATH}"
export HOME="${TEST_ROOT}/home"
mkdir -p "${HOME}"

if [[ -e "${SIMA_CLOUDEX_FORWARDER}" ]]; then
  echo "isolated forwarder path unexpectedly exists" >&2
  exit 1
fi

# A syntactically valid, deliberately non-secret profile reaches the normal
# connect path. The loopback discard endpoint makes the API stage fail quickly
# after connect has bootstrapped the missing forwarder.
PAIRING_CODE="$(python3 - <<'PY'
import base64
import json

profile = {
    "environment": "vulcan-staging",
    "api_url": "https://127.0.0.1:9",
    "requester": "cloudex-ci",
    "allocation_id": "0" * 32,
    "allocation_secret": "not-a-live-secret-" + "0" * 32,
}
print(base64.b64encode(json.dumps(profile).encode()).decode())
PY
)"

set +e
CONNECT_OUTPUT="$(printf '%s\n' "${PAIRING_CODE}" | "${SIMA_CLI}" cloudex connect --key-stdin 2>&1)"
CONNECT_STATUS=$?
set -e
if [[ "${CONNECT_STATUS}" -eq 0 ]]; then
  fail_with_command_output \
    "synthetic CloudEx connection unexpectedly succeeded" \
    "${CONNECT_OUTPUT}"
fi
if ! grep -F "CloudEx API is unavailable" <<<"${CONNECT_OUTPUT}" >/dev/null; then
  fail_with_command_output \
    "synthetic CloudEx connection did not reach the expected API failure" \
    "${CONNECT_OUTPUT}"
fi
if [[ ! -x "${SIMA_CLOUDEX_FORWARDER}" ]]; then
  fail_with_command_output \
    "CloudEx forwarder was not installed during synthetic connection" \
    "${CONNECT_OUTPUT}"
fi
INITIAL_VERSION="$("${SIMA_CLOUDEX_FORWARDER}" --version)"
test -n "${INITIAL_VERSION}"

# Corrupt the installed version in place. A successful update must download,
# checksum, and reinstall the package rather than merely accepting the symlink.
INSTALLED_BINARY="$(python3 - "${SIMA_CLOUDEX_FORWARDER}" <<'PY'
import os
import sys
print(os.path.realpath(sys.argv[1]))
PY
)"
printf '#!/usr/bin/env bash\necho stale-forwarder\n' >"${INSTALLED_BINARY}"
chmod 0755 "${INSTALLED_BINARY}"
test "$("${SIMA_CLOUDEX_FORWARDER}" --version)" = "stale-forwarder"

set +e
UPDATE_OUTPUT="$("${SIMA_CLI}" cloudex update 2>&1)"
UPDATE_STATUS=$?
set -e
if [[ "${UPDATE_STATUS}" -ne 0 ]]; then
  fail_with_command_output "CloudEx forwarder update failed" "${UPDATE_OUTPUT}"
fi
if ! grep -F "updated from develop" <<<"${UPDATE_OUTPUT}" >/dev/null; then
  fail_with_command_output \
    "CloudEx forwarder update did not report the expected branch" \
    "${UPDATE_OUTPUT}"
fi
UPDATED_VERSION="$("${SIMA_CLOUDEX_FORWARDER}" --version)"
test -n "${UPDATED_VERSION}"
test "${UPDATED_VERSION}" != "stale-forwarder"
test "${UPDATED_VERSION}" = "${INITIAL_VERSION}"

printf 'CloudEx forwarder fresh install and update passed: %s\n' "${UPDATED_VERSION}"
