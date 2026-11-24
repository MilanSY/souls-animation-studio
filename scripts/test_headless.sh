#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
blender="${BLENDER_BIN:-blender}"
if (( $# > 1 )); then
    printf 'Usage: bash %s [extension.zip]\n' "$0" >&2
    exit 2
fi

if (( $# == 1 )); then
    archive="$(realpath -- "$1")"
else
    # Derive the default filename from the same manifest used by Blender.
    archive="$(python3 -c '
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1])
with (root / "addon/blender_manifest.toml").open("rb") as stream:
    manifest = tomllib.load(stream)
print(root / "dist" / "{id}-{version}.zip".format_map(manifest))
' "$root")"
fi
if [[ ! -f "$archive" ]]; then
    printf 'ZIP introuvable : %s (lancer bash scripts/build_extension.sh)\n' "$archive" >&2
    exit 2
fi

scratch="$(mktemp -d "${TMPDIR:-/tmp}/souls-test.XXXXXXXX")"
trap 'rm -rf -- "$scratch"' EXIT
export BLENDER_USER_RESOURCES="$scratch/profile"
mkdir -p -- "$BLENDER_USER_RESOURCES"

"$blender" --background --factory-startup --offline-mode --command extension \
    validate "$archive"
"$blender" --background --factory-startup --offline-mode --command extension \
    install-file --repo user_default --enable "$archive"
# Load the isolated preferences saved by the real install in a fresh process.
"$blender" --background --offline-mode --python-exit-code 1 \
    --python "$root/tests/headless_smoke.py" -- "$archive"
