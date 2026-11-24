#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
blender="${BLENDER_BIN:-blender}"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/souls-build.XXXXXXXX")"
trap 'rm -rf -- "$scratch"' EXIT

mkdir -p -- "$root/dist" "$scratch/profile"
export BLENDER_USER_RESOURCES="$scratch/profile"
cp -R -- "$root/addon" "$scratch/addon"
cp -- "$root/LICENSE" "$scratch/addon/LICENSE"

"$blender" --background --factory-startup --offline-mode --command extension \
    validate "$scratch/addon"
"$blender" --background --factory-startup --offline-mode --command extension \
    build --source-dir "$scratch/addon" --output-dir "$root/dist"
