# SPDX-License-Identifier: GPL-3.0-or-later
"""Run in CPython; optionally run the Blender modal driver checks headlessly.

In Blender, SAS_MOTION_TEST_PYTHON can point to an external Python with NumPy.
All workers, jobs and checkpoints used as fixtures are local and synthetic.
"""

import json
from importlib import import_module
import os
from pathlib import Path
import shutil
import struct
import subprocess
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
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'addon'))
MODULE = os.environ.get('SAS_MOTION_TEST_MODULE') if bpy else None
process = import_module(f'{MODULE}.backends.motion_process' if MODULE else 'backends.motion_process')
CHECKPOINT_BYTES, JSON_LIMIT = process.CHECKPOINT_BYTES, process.JSON_LIMIT
MotionJob, MotionProcessError = process.MotionJob, process.MotionProcessError

WORKER = ROOT / 'backends/inbetweening/sprint4_worker.py'
PYTHON = os.environ.get('SAS_MOTION_TEST_PYTHON') or (shutil.which('python3') if bpy else sys.executable)


def request():
    rest = [[0, 0, 0], [.4, 0, 0], [.3, 0, 0]]
    identity = [1, 0, 0, 0]
    return dict(schema='sas.inbetween.request', version=1, request_id='fixture-a', fps=[24, 1],
                joints=[dict(id=name, parent=i-1 if i else None, rest_translation=rest[i],
                             rest_rotation=identity) for i, name in enumerate(('root', 'hinge', 'tip'))],
                anchors=[dict(frame=f, translations=rest, rotations=[identity]*3) for f in (1, 25)],
                sample_frames=list(range(2, 25)), contacts=[])


def wait_for(predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Le job de test ne se termine pas.')
        time.sleep(.01)


class ProcessTests(unittest.TestCase):
    def setUp(self):
        if not PYTHON:
            self.skipTest('Python externe introuvable')
        self.directory = tempfile.TemporaryDirectory(prefix='sas-motion-tests-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.weights = self.root / 'weights with spaces.f32'
        # Nonzero final bias; a linear/smooth fallback would return a zero root.
        self.weights.write_bytes(bytes(CHECKPOINT_BYTES - 24) + struct.pack('<6f', .25, 0, 0, 0, 0, 0))
        self.script = self.root / 'sprint4_worker.py'

    def start(self, body=None, *, worker=None, weights=None, payload=None, **kwargs):
        if worker is None:
            self.script.write_text(body, encoding='utf-8')
            worker = self.script
        job = MotionJob(PYTHON, worker, weights or self.weights, request() if payload is None else payload,
                        temp_root=self.root, **kwargs)
        self.addCleanup(self.close, job)
        return job

    def close(self, job):
        job.close()
        wait_for(lambda: not job.directory.exists())

    def test_preflight_missing_weights_wrong_size_and_device(self):
        for kwargs, diagnostic in (({'weights': self.root / 'absent.f32'}, 'Poids MLP.*absent'),
                                   ({'device': 'cuda'}, 'Périphérique incompatible'),
                                   ({'weights': self.root / 'short.f32'}, 'taille incorrecte')):
            (self.root / 'short.f32').write_bytes(b'')
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(MotionProcessError, diagnostic):
                self.start('raise AssertionError("must not launch")', **kwargs)
            self.assertEqual(list(self.root.glob('sas-mlp-*')), [])

    def test_reject_non_motion_envelope_before_launch(self):
        with self.assertRaisesRegex(MotionProcessError, 'Requête de mouvement'):
            self.start('raise AssertionError("must not launch")', payload=dict(request(), project='/private'))
        self.assertEqual(list(self.root.glob('sas-mlp-*')), [])

    def test_failed_spawn_cleans_directory(self):
        with patch('backends.motion_process.subprocess.Popen', side_effect=OSError('synthetic spawn failure')):
            with self.assertRaisesRegex(OSError, 'synthetic spawn failure'):
                self.start('pass')
        self.assertEqual(list(self.root.glob('sas-mlp-*')), [])

    def test_directory_arguments_and_environment(self):
        with patch.dict(os.environ, {'SAS_SECRET': 'not forwarded', 'PYTHONPATH': 'not forwarded'}):
            job = self.start('''
import json, os, sys
from pathlib import Path
assert set(os.listdir()) == {'request.json', 'stderr.log'}
assert not {'SAS_SECRET', 'PYTHONPATH'} & os.environ.keys()
assert sys.argv[1] == '--weights' and Path(sys.argv[2]).name == 'weights with spaces.f32'
assert sys.stdin.read() == ''
request = json.loads(Path('request.json').read_text())
assert request['request_id'] == 'fixture-a'
assert [joint['id'] for joint in request['joints']] == ['root', 'hinge', 'tip']
Path('response.json').write_text(json.dumps(dict(schema='sas.inbetween.response', version=1,
                                                request_id=request['request_id'], samples=[])))
''')
        wait_for(lambda: job.poll() is not None)
        self.assertEqual(job.result()['request_id'], 'fixture-a')
        self.close(job)

    def test_cancel_live_job_and_close_active_job(self):
        body = 'from pathlib import Path\nimport time\nPath("ready").touch()\ntime.sleep(60)\n'
        job = self.start(body)
        wait_for(lambda: (job.directory / 'ready').exists())
        self.assertIsNone(job.poll())
        job.cancel()
        job.cancel('should not replace original reason')
        wait_for(lambda: job.poll() is not None)
        with self.assertRaisesRegex(MotionProcessError, 'annulée'):
            job.result()
        self.close(job)
        job = self.start(body)
        self.close(job)
        with self.assertRaisesRegex(MotionProcessError, 'fermé'):
            job.result()

    def test_timeout(self):
        job = self.start('import time\ntime.sleep(60)\n', timeout=.05)
        wait_for(lambda: job.poll() is not None)
        with self.assertRaisesRegex(MotionProcessError, 'Délai.*dépassé'):
            job.result()

    def test_crash_never_reads_even_published_response(self):
        job = self.start('''
from pathlib import Path
import sys
Path('response.json').write_text('{"schema":"sas.inbetween.response","version":1,"request_id":"fixture-a","samples":[]}')
sys.stderr.write('synthetic crash\\n')
sys.exit(17)
''')
        wait_for(lambda: job.poll() is not None)
        with self.assertRaisesRegex(MotionProcessError, 'code 17.*synthetic crash'):
            job.result()

    def test_invalid_or_missing_response_after_success(self):
        for value in (None, b'{', b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e400}', b'x' * (JSON_LIMIT+1),
                      b'{"schema":"sas.inbetween.response","version":1,"request_id":"wrong","samples":[]}'):
            with self.subTest(prefix=None if value is None else value[:80]):
                job = self.start('pass' if value is None else
                                 f'from pathlib import Path\nPath("response.json").write_bytes({value!r})')
                wait_for(lambda: job.poll() is not None)
                with self.assertRaisesRegex(MotionProcessError, 'Réponse MLP invalide'):
                    job.result()
                self.close(job)

    def require_numpy(self):
        probe = subprocess.run([PYTHON, '-E', '-s', '-B', '-c', 'import numpy'], cwd=self.root,
                               capture_output=True, timeout=10)
        if probe.returncode:
            self.skipTest('NumPy absent du Python externe')

    def test_real_numpy_worker(self):
        self.require_numpy()
        job = self.start(worker=WORKER)
        wait_for(lambda: job.poll() is not None)
        result = job.result()
        self.assertEqual(result['request_id'], 'fixture-a')
        self.assertEqual([row['frame'] for row in result['samples']], request()['sample_frames'])
        self.assertEqual(result['samples'][0]['translations'][0], [.25, 0, 0])
        # Esc during the post-exit validation/bake window must still discard.
        job.cancel()
        with self.assertRaisesRegex(MotionProcessError, 'annulée'):
            job.result()


if bpy is not None:
    sys.path.insert(0, str(ROOT / 'tests'))
    from headless_ai_retarget import MAPPING, NAMES, original_keys, rig_fixture, row, seed, time as set_frame
    ui = import_module(f'{MODULE or "addon"}.operators.ai_inbetween')

    class BlenderTests(unittest.TestCase):
        def test_operator_modal_transitions(self):
            if not PYTHON:
                self.skipTest('Python externe introuvable')
            if not MODULE:
                for cls in ui.CLASSES:
                    bpy.utils.register_class(cls)
                    self.addCleanup(bpy.utils.unregister_class, cls)
            self.addCleanup(ui.cancel_active_job)

            scene = bpy.context.scene
            scene.render.fps = 24
            scene.render.fps_base = 1
            scene.unit_settings.scale_length = 1
            rig = rig_fixture('Motion modal fixture', NAMES, 1)
            source, slot = seed(rig, [row(f, (0, 0, 0), (0, 0, 0)) for f in (1, 25)], 1)
            set_frame(7.5)
            source_keys = original_keys(source, slot)

            with tempfile.TemporaryDirectory(prefix='sas-modal-test-') as tmp:
                root = Path(tmp)
                weights = root / 'weights.f32'
                weights.write_bytes(bytes(CHECKPOINT_BYTES))
                slow = root / 'sprint4_worker.py'
                slow.write_text('import time\ntime.sleep(60)\n', encoding='utf-8')
                crash_dir = root / 'crash'
                crash_dir.mkdir()
                crash = crash_dir / 'sprint4_worker.py'
                crash.write_text('raise SystemExit(17)\n', encoding='utf-8')
                probe = subprocess.run([PYTHON, '-E', '-s', '-B', '-c', 'import numpy'], cwd=root,
                                       capture_output=True, timeout=10)
                modes = [('escape', slow), ('button', slow), ('load', slow), ('crash', crash)]
                if probe.returncode == 0:
                    modes.append(('success', WORKER))
                else:
                    print('Blender → worker → Action non testé : NumPy externe absent.')

                for mode, worker in modes:
                    with self.subTest(mode=mode):
                        # Real RNA scene/armature/actions, with synthetic event delivery
                        # and progress API because background Blender has no UI loop.
                        progress, reports = [], []
                        timer = SimpleNamespace(time_duration=0.)
                        wm = SimpleNamespace(windows=[], event_timer_add=lambda *a, **k: timer,
                                             event_timer_remove=lambda _: progress.append('removed'),
                                             progress_begin=lambda *a: progress.append('begin'),
                                             progress_end=lambda: progress.append('end'),
                                             progress_update=progress.append, modal_handler_add=lambda _: None)
                        context = SimpleNamespace(scene=scene, view_layer=bpy.context.view_layer,
                                                  window_manager=wm, workspace=SimpleNamespace(status_text_set=lambda _: None),
                                                  window=object(), mode='OBJECT')
                        driver = SimpleNamespace(poll=lambda _: True, report=lambda *args: reports.append(args),
                                                 rig_name=rig.name, root_bone=MAPPING['root'],
                                                 hinge_bone=MAPPING['hinge'], tip_bone=MAPPING['tip'],
                                                 start_frame=1, end_frame=25, timeout_seconds=10, trust_local_files=True,
                                                 python_path=PYTHON, worker_path=str(worker), weights_path=str(weights))
                        for name in ('execute', 'modal', '_redraw', '_show_stage', '_request_cancel', '_failed', '_finish'):
                            setattr(driver, name, getattr(ui.SAS_OT_ai_inbetween, name).__get__(driver))

                        def tick():
                            timer.time_duration += .1
                            return driver.modal(context, SimpleNamespace(type='TIMER'))

                        before = set(bpy.data.actions)
                        try:
                            self.assertEqual(driver.execute(context), {'RUNNING_MODAL'}, reports)
                            self.assertEqual(tick(), {'RUNNING_MODAL'}, reports)
                            directory = driver._job.directory
                            if mode == 'escape':
                                driver.modal(context, SimpleNamespace(type='ESC', value='PRESS'))
                            elif mode == 'button':
                                self.assertEqual(bpy.ops.sas.cancel_ai_inbetween(), {'FINISHED'})
                            elif mode == 'load':
                                ui._before_load(None)
                            outcome = []

                            def completed():
                                outcome[:] = tick()
                                return outcome[0] in {'FINISHED', 'CANCELLED'}

                            wait_for(completed)
                            wait_for(lambda: not directory.exists())
                            new_actions = set(bpy.data.actions) - before
                            if mode == 'success':
                                self.assertEqual(outcome, ['FINISHED'], reports)
                                self.assertEqual(progress, ['begin', 0, 1, 2, 3, 'removed', 'end'])
                                draft, = new_actions
                                self.assertTrue(draft.use_fake_user)
                                self.assertEqual(original_keys(draft, draft.slots[0], range(2, 25)), source_keys)
                            else:
                                self.assertEqual(outcome, ['CANCELLED'], reports)
                                self.assertEqual(progress[-2:], ['removed', 'end'])
                                self.assertFalse(new_actions)
                                if mode == 'crash':
                                    self.assertTrue(any('code 17' in text for _, text in reports))
                            self.assertEqual(rig.animation_data.action, source)
                            self.assertEqual(rig.animation_data.action_slot, slot)
                            self.assertEqual(original_keys(source, slot), source_keys)
                            self.assertEqual((scene.frame_current, scene.frame_subframe), (7, .5))
                            self.assertIsNone(ui._active)
                            self.assertNotIn(ui._before_load, bpy.app.handlers.load_pre)
                        finally:
                            ui.cancel_active_job()


if __name__ == '__main__':
    suite = unittest.main(argv=[sys.argv[0]], exit=False)
    if not suite.result.wasSuccessful():
        raise RuntimeError('Tests du processus MLP en échec.')
