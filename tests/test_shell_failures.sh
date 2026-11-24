#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Prove that the top-level launcher propagates both Blender CLI and Python failures.
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if (( $# != 1 )); then
    printf 'Usage: bash %s extension.zip\n' "$0" >&2
    exit 2
fi
archive="$(realpath -- "$1")"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/opencode/sas-negative.XXXXXXXX" 2>/dev/null ||
    mktemp -d "${TMPDIR:-/tmp}/sas-negative.XXXXXXXX")"
trap 'rm -rf -- "$scratch"' EXIT

unset SOULS_TEST_GLB
expect_failure() {
    local name="$1" expected_match="$2" status=0
    shift 2
    "$@" >"$scratch/$name.log" 2>&1 || status=$?
    if (( status == 0 )) || ! grep -Fq -- "$expected_match" "$scratch/$name.log"; then
        cat "$scratch/$name.log" >&2
        printf 'Expected failure %s was not propagated as intended (status %d)\n' "$name" "$status" >&2
        return 1
    fi
    printf 'Expected failure %s propagated (shell status %d)\n' "$name" "$status"
}

printf 'not a zip\n' >"$scratch/corrupt.zip"
expect_failure corrupt_zip 'File is not a zip file' \
    bash "$root/scripts/test_headless.sh" "$scratch/corrupt.zip"

export SAS_TEST_REAL_BLENDER
SAS_TEST_REAL_BLENDER="$(command -v -- "${BLENDER_BIN:-blender}")"
cat >"$scratch/blender-with-failing-assertion" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
args=("$@")
for i in "${!args[@]}"; do
    if [[ "${args[i]}" == --python && "${args[i+1]}" == */tests/headless_smoke.py ]]; then
        # Inject a failing Python script, but leave the launcher's --python-exit-code flag intact.
        args[i]='--python-expr'
        args[i+1]='raise AssertionError("SAS deliberate smoke assertion")'
        break
    fi
done
exec "$SAS_TEST_REAL_BLENDER" "${args[@]}"
EOF
chmod +x "$scratch/blender-with-failing-assertion"
expect_failure python_assertion 'AssertionError: SAS deliberate smoke assertion' \
    env BLENDER_BIN="$scratch/blender-with-failing-assertion" \
    bash "$root/scripts/test_headless.sh" "$archive"
