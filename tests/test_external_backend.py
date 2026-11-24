# SPDX-License-Identifier: GPL-3.0-or-later
"""Run with CPython and optionally Blender. All data/processes are synthetic."""

from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


try:
    import bpy
except ImportError:
    bpy = None

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "backends/unirig/fixtures"
BACKENDS = ROOT / "addon/backends"
sys.path[:0] = [str(ROOT), str(ROOT / "addon"), str(BACKENDS)]
from backends.rig_exchange import make_request, read_json, validate_response, vertex_mapping
from backends.process import ExternalJob, unirig_command


def fixtures():
    return tuple(read_json(FIXTURES / f"{kind}.json") for kind in ("request", "response"))


class ExchangeTest(unittest.TestCase):
    def test_reordered_vertices(self):
        _, weights = validate_response(*fixtures())
        self.assertEqual(weights, [[[0, 1]], [[0, 0.75], [1, 0.25]], [[1, 1]], [[0, 0.25], [1, 0.75]]])

    def test_invalid_results(self):
        req, original = fixtures()
        for label, mutate in (
            ("version", lambda r: r.update(version=True)),
            ("mesh_id", lambda r: r.update(mesh_id="other")),
            ("space", lambda r: r.update(space="y-up")),
            ("count", lambda r: r["vertices"].append([0.2, 0.2, 0.2])),
            ("coordinate", lambda r: r["vertices"][0].__setitem__(0, 0.1)),
            ("topology", lambda r: r["triangles"][0].__setitem__(2, 0)),
            ("parent", lambda r: r["bones"][1].update(parent=1)),
            ("name", lambda r: r["bones"][1].update(name="Root")),
            ("roll", lambda r: r["bones"][0].update(z_axis=[0, 0, 1])),
            ("zero length", lambda r: r["bones"][0].update(tail=[0, 0, 0])),
            ("missing weight", lambda r: r["weights"].pop()),
            ("sum", lambda r: r["weights"][0][0].__setitem__(1, 0.8)),
            ("nan", lambda r: r["weights"][0][0].__setitem__(1, float("nan"))),
            ("negative", lambda r: r["weights"][0][0].__setitem__(1, -1)),
            ("bone index", lambda r: r["weights"][0][0].__setitem__(0, 2)),
            ("duplicate influence", lambda r: r["weights"].__setitem__(0, [[0, 0.5], [0, 0.5]])),
            ("bool index", lambda r: r["weights"][0][0].__setitem__(0, True)),
            ("unknown field", lambda r: r.update(note="unexpected")),
        ):
            with self.subTest(label=label), self.assertRaises(ValueError):
                response = deepcopy(original)
                mutate(response)
                validate_response(req, response)

    def test_ambiguous_vertices(self):
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            vertex_mapping([[0, 0, 0], [0.000001, 0, 0]], [[0, 0, 0], [0.000001, 0, 0]])

    def test_json_is_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.json"
            for value in ('{"x":1,"x":2}', '{"x":NaN}', '{'):
                path.write_text(value, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_json(path)


def wait_for(condition):
    deadline = time.monotonic() + 5
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError("Synthetic process did not reach expected state.")
        time.sleep(0.015)


@unittest.skipUnless(bpy is None and sys.platform == "linux", "CPython/Linux process tests")
class ProcessTest(unittest.TestCase):
    def start_job(self, mode, **kwargs):
        req, _ = fixtures()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.dict(os.environ, SAS_TEST_PROJECT_PATH="private/project"):
            job = ExternalJob([sys.executable, "-B", str(FIXTURES / "fake_worker.py"), mode], req,
                              temp_root=tmp.name, **kwargs)

        def cleanup():
            job.close()
            wait_for(lambda: not job.directory.exists())

        self.addCleanup(cleanup)
        return job

    def test_success_progress_and_cleanup(self):
        job = self.start_job("success")
        wait_for(lambda: job.poll() is not None)
        self.assertEqual(job.progress(), 4)
        self.assertEqual(job.result(), fixtures()[1])
        job.close()
        self.assertFalse(job.directory.exists())

    def test_fail_and_missing_response(self):
        for mode, error in (("fail", "synthetic CUDA failure"), ("missing", "response.json")):
            with self.subTest(mode=mode):
                job = self.start_job(mode)
                wait_for(lambda: job.poll() is not None)
                with self.assertRaisesRegex((ValueError, RuntimeError), error):
                    job.result()

    def test_cancel_including_descendant(self):
        job = self.start_job("wait")
        pid_path = job.directory / "child.pid"
        wait_for(lambda: pid_path.exists() and pid_path.stat().st_size > 0)
        pid = int(pid_path.read_text())
        self.assertIsNone(job.poll())
        job.cancel()
        wait_for(lambda: job.poll() is not None)

        def descendant_dead():
            try:
                return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] == "Z"
            except FileNotFoundError:
                return True

        wait_for(descendant_dead)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            job.result()

    def test_timeout_and_configuration(self):
        job = self.start_job("wait", timeout=0)
        wait_for(lambda: job.poll() is not None)
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            job.result()
        with self.assertRaisesRegex(ValueError, "absolute"):
            unirig_command("python", "UniRig")
        with self.assertRaisesRegex(ValueError, "Invalid UniRig checkout"):
            unirig_command(sys.executable, str(FIXTURES))

    def test_worker_routes_to_verified_cli_shapes(self):
        import unirig_worker as worker

        commands = []

        def fake_run(command, *, check, env):
            self.assertTrue(check)
            self.assertEqual(env["PYTHONPATH"], str(checkout))
            args = dict(arg[2:].split("=", 1) for arg in command if arg.startswith("--"))
            commands.append(command[2:])
            if "src.data.extract" in command:
                output = Path(args["output_dir"]) / Path(args["input"]).stem / "raw_data.npz"
            else:
                output = Path(args["output"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"synthetic output; not a model or FBX")

        start = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = root / "trusted checkout"
            tasks = checkout / "configs/task"
            tasks.mkdir(parents=True)
            for task, checkpoint in worker.CHECKPOINTS.items():
                (tasks / f"{task}.yaml").write_text("{}", encoding="utf-8")
                weights = checkout / "experiments" / checkpoint
                weights.parent.mkdir(parents=True)
                weights.write_bytes(b"not a checkpoint")
            job = root / "job"
            job.mkdir()
            (job / "request.json").write_bytes((FIXTURES / "request.json").read_bytes())
            os.chdir(job)
            try:
                modules = dict(torch=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True)),
                               yaml=SimpleNamespace(safe_load=lambda _: {}, safe_dump=repr))
                with (patch.dict(sys.modules, modules), patch.object(worker.subprocess, "run", fake_run),
                      patch.object(worker, "convert_fbx", return_value=fixtures()[1])):
                    worker.main(checkout)
                self.assertEqual([args[0] for args in commands],
                                 ["-m", str(checkout / "run.py"), "-m", str(checkout / "run.py"), "-m"])
                self.assertIn("--data_name=raw_data.npz", commands[3])
                self.assertIn("--source=skin.fbx", commands[4])
                self.assertEqual(read_json(job / "response.json"), fixtures()[1])
                self.assertEqual((job / "progress").read_text(), "6")
                for task, relative in worker.CHECKPOINTS.items():
                    self.assertIn(str(checkout / "experiments" / relative),
                                  (job / f"{task}.yaml").read_text())
            finally:
                os.chdir(start)


@unittest.skipUnless(bpy is not None, "Blender integration tests")
class BlenderTest(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)

    def test_import_weights_and_source_unchanged(self):
        from addon.backends.blender_rig import import_result, snapshot_mesh
        from addon.operators.ai_rigging import CLASSES, cancel_active_job
        # Validate RNA registration without touching the extension registration.
        bpy.utils.register_class(CLASSES[0])
        self.addCleanup(bpy.utils.unregister_class, CLASSES[0])
        self.addCleanup(cancel_active_job)
        request, response = fixtures()
        data = bpy.data.meshes.new("Source geometry")
        data.from_pydata(request["vertices"], [], request["triangles"])
        obj = bpy.data.objects.new("Source mesh", data)
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        obj.location = (2, -3, 4)
        obj.rotation_euler.z = 0.5
        obj.scale = (1.0, 1.5, 2.0)
        obj.vertex_groups.new(name="Source only").add([0], 0.4, "REPLACE")
        snapshot = snapshot_mesh(bpy.context, obj)
        response["mesh_id"] = snapshot.request["mesh_id"]
        response["vertices"] = [snapshot.request["vertices"][i] for i in (2, 0, 3, 1)]
        rig, skin = import_result(bpy.context, snapshot, response)
        self.assertEqual(rig.data.bones["Tip"].parent, rig.data.bones["Root"])
        self.assertEqual(skin.modifiers[0].object, rig)
        self.assertAlmostEqual(skin.vertex_groups["Tip"].weight(1), 0.25)
        self.assertAlmostEqual(skin.vertex_groups["Tip"].weight(2), 1.0)
        self.assertAlmostEqual(obj.vertex_groups["Source only"].weight(0), 0.4)
        self.assertNotIn("Source only", skin.vertex_groups)
        self.assertFalse(obj.modifiers)
        self.assertEqual(bpy.context.active_object, obj)
        self.assertEqual(list(bpy.context.selected_objects), [obj])
        self.assertLess(max(abs(x-y) for a, b in zip(skin.matrix_world, obj.matrix_world)
                            for x, y in zip(a, b)), 1e-5)

        rig.pose.bones["Tip"].location.x = 0.5
        bpy.context.view_layer.update()
        evaluated = skin.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices
        self.assertLess((evaluated[0].co - obj.data.vertices[0].co).length, 1e-5)
        self.assertGreater((evaluated[2].co - obj.data.vertices[2].co).length, 0.4)

        before = (len(bpy.data.objects), len(bpy.data.armatures), len(bpy.data.meshes))
        broken = deepcopy(response)
        broken["weights"][0] = [[1, 0.5]]
        with self.assertRaisesRegex(ValueError, "sum"):
            import_result(bpy.context, snapshot, broken)
        obj.location.x += 1
        with self.assertRaisesRegex(ValueError, "changed"):
            import_result(bpy.context, snapshot, response)
        self.assertEqual(before, (len(bpy.data.objects), len(bpy.data.armatures), len(bpy.data.meshes)))

    def test_synthetic_obj_fbx_boundary(self):
        from mathutils import Matrix
        from addon.backends.blender_rig import import_result, snapshot_mesh
        from unirig_worker import convert_fbx, obj_file

        start = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                request, response = fixtures()
                request = make_request([[x, y-0.5, z-0.5] for x, y, z in request["vertices"]],
                                       request["triangles"])
                obj_file(request)
                bpy.ops.wm.obj_import(filepath=str(Path("mesh.obj").absolute()))
                source = bpy.context.active_object
                bpy.context.view_layer.update()
                vertex_mapping(request["vertices"], [list(source.matrix_world @ v.co) for v in source.data.vertices])
                # Bake the OBJ import transform before using mesh-local exchange coordinates.
                source.data.transform(source.matrix_world)
                source.matrix_world = Matrix.Identity(4)
                snapshot = snapshot_mesh(bpy.context, source)
                response["mesh_id"] = snapshot.request["mesh_id"]
                mapping = vertex_mapping(request["vertices"], [list(v.co) for v in source.data.vertices])
                response["vertices"] = [snapshot.request["vertices"][mapping.index(i)] for i in (2, 0, 3, 1)]
                rig, skin = import_result(bpy.context, snapshot, response)
                bpy.data.objects.remove(source, do_unlink=True)
                self.assertEqual(bpy.ops.export_scene.fbx(filepath=str(Path("merged.fbx").absolute()),
                                                         add_leaf_bones=True, bake_anim=False), {"FINISHED"})
                converted = convert_fbx(snapshot.request)
                _, rows = validate_response(snapshot.request, converted)
                self.assertEqual(rows[mapping.index(2)], [[1, 1.0]])
            finally:
                os.chdir(start)


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
