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
print(root / "dist" / "{id}-{version}-dev-sprint-5a.zip".format_map(manifest))
' "$root")"
fi
if [[ ! -f "$archive" ]]; then
    printf 'ZIP introuvable : %s (lancer bash scripts/build_extension.sh)\n' "$archive" >&2
    exit 2
fi
if [[ -n "${SOULS_TEST_GLB:-}" && ( -d "$SOULS_TEST_GLB" || -d "$SOULS_TEST_GLB.json" ) ]]; then
    printf 'SOULS_TEST_GLB doit pointer vers un fichier, pas un répertoire.\n' >&2
    exit 2
fi

scratch="$(mktemp -d "${TMPDIR:-/tmp}/opencode/souls-test.XXXXXXXX" 2>/dev/null ||
    mktemp -d "${TMPDIR:-/tmp}/souls-test.XXXXXXXX")"
trap 'rm -rf -- "$scratch"' EXIT
# These override BLENDER_USER_RESOURCES when set in the caller's environment.
unset BLENDER_USER_CONFIG BLENDER_USER_SCRIPTS BLENDER_USER_EXTENSIONS BLENDER_USER_DATAFILES
export BLENDER_USER_RESOURCES="$scratch/profile"
mkdir -p -- "$BLENDER_USER_RESOURCES"

printf 'ZIP testé : %s\n' "$archive"
"$blender" --background --factory-startup --offline-mode --command extension \
    validate "$archive"
ext_id="$(python3 -c '
import sys
import tomllib
from zipfile import ZipFile
with ZipFile(sys.argv[1]) as archive:
    print(tomllib.loads(archive.read("blender_manifest.toml").decode())["id"])
' "$archive")"
"$blender" --background --factory-startup --offline-mode --command extension \
    install-file --repo user_default --enable "$archive"
# Load the isolated preferences saved by the real install in a fresh process.
"$blender" --background --offline-mode --python-exit-code 1 \
    --python "$root/tests/headless_smoke.py" -- "$archive"
"$blender" --background --offline-mode --python-exit-code 1 \
    --python "$root/tests/headless_workflow.py" -- "$scratch/reference.glb"
"$blender" --background --offline-mode --python-exit-code 1 \
    --python "$root/tests/headless_smartpose.py" -- "$scratch"
"$blender" --background --offline-mode --disable-autoexec "$scratch/smartpose.blend" \
    --python-exit-code 1 --python "$root/tests/headless_smartpose.py" -- "$scratch" --reopen
"$blender" --background --offline-mode --python-exit-code 1 \
    --python "$root/tests/headless_rigging_sprint3.py"
"$blender" --background --offline-mode --python-exit-code 1 \
    --python "$root/tests/headless_rigging_bridge.py"
"$blender" --background --offline-mode --python-exit-code 1 \
    --python "$root/tests/headless_contacts_sprint3.py" -- "$scratch"
"$blender" --background --offline-mode --python-exit-code 1 \
    --python "$root/tests/headless_accessories.py" -- "$scratch"
"$blender" --background --offline-mode --disable-autoexec --python-exit-code 1 \
    --python "$root/tests/headless_humanoid_retarget.py"
"$blender" --background --offline-mode --disable-autoexec --python-exit-code 1 \
    --python "$root/tests/headless_humanoid_contacts.py" -- "$scratch"
python3 "$root/tests/test_external_backend.py"
"$blender" --background --offline-mode --python-exit-code 1 \
    --python "$root/tests/test_external_backend.py"
python3 "$root/backends/inbetweening/test_cpu.py"
python3 "$root/tests/test_humanoid_bench.py"
python3 "$root/tests/test_humanoid_continuity.py"
"$blender" --background --factory-startup --offline-mode --python-exit-code 1 \
    --python "$root/backends/inbetweening/test_blender.py"

python3 "$root/tests/test_motion_process.py"
export SAS_MOTION_TEST_MODULE="bl_ext.user_default.$ext_id"
"$blender" --background --offline-mode --python-exit-code 1 --python "$root/tests/test_motion_process.py"
motion_args=()
if [[ -n "${SAS_MOTION_TEST_PYTHON:-}" ]]; then
    motion_args+=(--proof "$root/demo/sprint4-planar-preuve.json" --python "$SAS_MOTION_TEST_PYTHON"
        --weights "$root/demo/sprint4-planar.f32" --glb "$scratch/neural-motion.glb")
    if [[ "${SOULS_TEST_GODOT:-0}" == 1 ]]; then
        motion_args+=(--godot "${GODOT_BIN:-godot}")
    fi
fi
"$blender" --background --offline-mode --python-exit-code 1 \
    --python "$root/tests/headless_ai_retarget.py" -- "${motion_args[@]}"

if [[ "${SOULS_TEST_GODOT:-0}" == 1 ]]; then
    bash "$root/tests/test_godot_import.sh" "$scratch/reference.glb"
    bash "$root/tests/test_godot_import.sh" "$scratch/combined.glb" \
        "$scratch/combined.glb.json" "$scratch/accessory.glb"
fi

"$blender" --background --offline-mode --command extension remove "$ext_id"
# A successful command alone doesn't prove that the actual installed add-on is gone.
"$blender" --background --offline-mode --python-exit-code 1 \
    --python "$root/tests/headless_uninstalled.py" -- "$ext_id"

# Optionally keep the exact GLB that passed, for a separate Godot import probe.
if [[ -n "${SOULS_TEST_GLB:-}" ]]; then
    cp -T -- "$scratch/reference.glb" "$SOULS_TEST_GLB"
    cp -T -- "$scratch/reference.glb.json" "$SOULS_TEST_GLB.json"
    printf 'GLB et dimensions de référence : %s[.json]\n' "$SOULS_TEST_GLB"
fi
