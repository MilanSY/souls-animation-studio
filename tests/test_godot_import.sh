#!/usr/bin/env bash
set -euo pipefail

if (( $# < 1 || $# > 2 )); then
    printf 'Usage: bash %s character.glb [reference.json]\n' "$0" >&2
    exit 2
fi

tests="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
glb="$(realpath -e -- "$1")"
args=("$glb")
if (( $# == 2 )); then
    args+=("$(realpath -e -- "$2")")
elif [[ -f "$glb.json" ]]; then
    args+=("$glb.json")
fi

scratch="$(mktemp -d "${TMPDIR:-/tmp}/opencode/souls-godot.XXXXXXXX" 2>/dev/null ||
    mktemp -d "${TMPDIR:-/tmp}/souls-godot.XXXXXXXX")"
trap 'rm -rf -- "$scratch"' EXIT
export XDG_CONFIG_HOME="$scratch/config"
export XDG_CACHE_HOME="$scratch/cache"
export XDG_DATA_HOME="$scratch/data"
printf 'config_version=5\n\n[application]\nconfig/name="SAS GLB Import Probe"\n' > "$scratch/project.godot"
cp -- "$tests/godot_probe.gd" "$scratch/"
"${GODOT_BIN:-godot}" --headless --path "$scratch" --script res://godot_probe.gd -- "${args[@]}"
