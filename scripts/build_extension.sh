#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
blender="${BLENDER_BIN:-blender}"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/opencode/souls-build.XXXXXXXX" 2>/dev/null ||
    mktemp -d "${TMPDIR:-/tmp}/souls-build.XXXXXXXX")"
trap 'rm -rf -- "$scratch"' EXIT

mkdir -p -- "$root/dist" "$scratch/profile"
unset BLENDER_USER_CONFIG BLENDER_USER_SCRIPTS BLENDER_USER_EXTENSIONS BLENDER_USER_DATAFILES
export BLENDER_USER_RESOURCES="$scratch/profile"
cp -R -- "$root/addon" "$scratch/addon"
cp -- "$root/LICENSE" "$scratch/addon/LICENSE"

# Keep the published archive intact, although the Blender manifest version is unchanged.
archive="$(python3 -c '
import sys, tomllib
with open(sys.argv[1], "rb") as stream:
    print("{id}-{version}-dev-sprint-5a.zip".format_map(tomllib.load(stream)))
' "$scratch/addon/blender_manifest.toml")"
"$blender" --background --factory-startup --offline-mode --command extension \
    validate "$scratch/addon"
"$blender" --background --factory-startup --offline-mode --command extension \
    build --source-dir "$scratch/addon" --output-filepath "$root/dist/$archive"
