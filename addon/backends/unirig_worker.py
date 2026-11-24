# SPDX-License-Identifier: GPL-3.0-or-later
"""Entry point for the *external* UniRig Python. Never imported by the operator."""

from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys

from rig_exchange import read_json, validate_request, validate_response, write_json


SKELETON = "quick_inference_skeleton_articulationxl_ar_256"
SKIN = "quick_inference_unirig_skin"
CHECKPOINTS = {
    SKELETON: "skeleton/articulation-xl_quantization_256/model.ckpt",
    SKIN: "skin/articulation-xl/model.ckpt",
}


def progress(step):
    Path("progress.next").write_text(str(step), encoding="ascii")
    Path("progress.next").replace("progress")


def obj_file(request):
    with Path("mesh.obj").open("w", encoding="ascii") as stream:
        stream.write("o SAS_input\n")
        for x, y, z in request["vertices"]:
            stream.write(f"v {x:.10g} {z:.10g} {-y:.10g}\n")
        for a, b, c in request["triangles"]:
            stream.write(f"f {a+1} {b+1} {c+1}\n")


def require_output(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"UniRig did not produce {path}.")


def convert_fbx(request):
    import bpy
    from mathutils import Vector

    bpy.ops.wm.read_factory_settings(use_empty=True)
    result = bpy.ops.import_scene.fbx(filepath=str(Path("merged.fbx").absolute()),
                                      use_anim=False, use_image_search=False,
                                      ignore_leaf_bones=True)
    if result != {"FINISHED"}:
        raise RuntimeError("Could not decode UniRig's merged FBX.")
    objects = bpy.context.scene.objects
    meshes = [obj for obj in objects if obj.type == "MESH"]
    rigs = [obj for obj in objects if obj.type == "ARMATURE"]
    if len(meshes) != 1 or len(rigs) != 1:
        raise ValueError("UniRig FBX must contain exactly one mesh and one armature.")
    mesh, rig = meshes[0], rigs[0]
    if [mod.object for mod in mesh.modifiers if mod.type == "ARMATURE"] != [rig]:
        raise ValueError("UniRig FBX mesh is not bound to its predicted armature.")
    ordered = []
    frontier = [bone for bone in rig.data.bones if bone.parent is None]
    while frontier:
        bone = frontier.pop()
        ordered.append(bone)
        frontier.extend(reversed(tuple(bone.children)))
    indices = {bone.name: i for i, bone in enumerate(ordered)}
    bones = []
    for bone in ordered:
        axis = (rig.matrix_world @ bone.matrix_local).to_3x3() @ Vector((0, 0, 1))
        bones.append(dict(name=bone.name, parent=indices[bone.parent.name] if bone.parent else None,
                          head=list(rig.matrix_world @ bone.head_local),
                          tail=list(rig.matrix_world @ bone.tail_local), z_axis=list(axis.normalized())))
    mesh.data.calc_loop_triangles()
    rows = []
    for vertex in mesh.data.vertices:
        row = []
        for entry in vertex.groups:
            if entry.weight == 0:
                continue  # Blender can retain zero-weight group membership.
            name = mesh.vertex_groups[entry.group].name
            if name not in indices:
                raise ValueError(f"Unknown weighted bone in UniRig FBX: {name}")
            row.append([indices[name], entry.weight])
        rows.append(row)
    return dict(schema="sas.rig.response", version=1, space=request["space"],
                mesh_id=request["mesh_id"], bones=bones, weights=rows,
                vertices=[list(mesh.matrix_world @ v.co) for v in mesh.data.vertices],
                triangles=[list(face.vertices) for face in mesh.data.loop_triangles])


def main(checkout):
    import torch
    import yaml

    if not torch.cuda.is_available():
        raise RuntimeError("UniRig requires CUDA in the external Python; no CPU fallback.")
    request = read_json(Path("request.json"))
    validate_request(request)
    obj_file(request)
    # Upstream run.py resolves some configs from cwd. Only configs are linked;
    # logs, output, task overrides and intermediates stay in the job directory.
    Path("configs").symlink_to(checkout / "configs", target_is_directory=True)
    env = dict(os.environ, PYTHONPATH=str(checkout), PYTHONNOUSERSITE="1")

    def run(*args):
        subprocess.run([sys.executable, "-B", *args], check=True, env=env)

    def extract(source, npz_dir):
        run("-m", "src.data.extract", "--config=configs/data/quick_inference.yaml",
            "--require_suffix=obj,fbx", "--faces_target_count=50000", "--num_runs=1",
            "--force_override=true", "--id=0", "--time=" + datetime.now().strftime("%Y%m%d-%H%M%S"),
            "--input=" + source, "--output_dir=" + npz_dir)
        require_output(Path(npz_dir) / Path(source).stem / "raw_data.npz")

    def predict(task_name, source, output, npz_dir, *extra):
        task = yaml.safe_load(Path(f"configs/task/{task_name}.yaml").read_text(encoding="utf-8"))
        checkpoint = checkout / "experiments" / CHECKPOINTS[task_name]
        require_output(checkpoint)
        # Absolute path bypasses upstream's named Hugging Face download mapping.
        task["resume_from_checkpoint"] = str(checkpoint)
        config = f"{task_name}.yaml"
        Path(config).write_text(yaml.safe_dump(task), encoding="utf-8")
        run(str(checkout / "run.py"), "--task=" + config, "--seed=12345", "--input=" + source,
            "--output=" + output, "--npz_dir=" + npz_dir, *extra)
        require_output(output)  # upstream writers can swallow export failures

    progress(1)
    extract("mesh.obj", "skeleton_npz")
    progress(2)
    predict(SKELETON, "mesh.obj", "skeleton.fbx", "skeleton_npz")
    progress(3)
    extract("skeleton.fbx", "skin_npz")
    progress(4)
    predict(SKIN, "skeleton.fbx", "skin.fbx", "skin_npz", "--data_name=raw_data.npz")
    progress(5)
    run("-m", "src.inference.merge", "--require_suffix=obj,fbx", "--num_runs=1", "--id=0",
        "--source=skin.fbx", "--target=mesh.obj", "--output=merged.fbx")
    require_output("merged.fbx")
    progress(6)
    response = convert_fbx(request)
    validate_response(request, response)
    write_json(Path("response.next"), response)
    Path("response.next").replace("response.json")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
