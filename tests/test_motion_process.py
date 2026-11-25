# SPDX-License-Identifier: GPL-3.0-or-later
"""Run in CPython; optionally run the Blender modal driver checks headlessly.

In Blender, SAS_MOTION_TEST_PYTHON can point to an external Python with NumPy.
All workers, jobs and checkpoints used as fixtures are local and synthetic.
Linux validates child/grandchild termination through /proc. Windows uses a
gated Job Object launch; tree coverage below is skipped without Linux /proc.
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
profiles = import_module(f'{MODULE}.backends.motion_profiles' if MODULE else 'backends.motion_profiles')
CHECKPOINT_BYTES, JSON_LIMIT = process.CHECKPOINT_BYTES, process.JSON_LIMIT
MotionJob, MotionProcessError = process.MotionJob, process.MotionProcessError

WORKER = ROOT / 'backends/inbetweening/sprint4_worker.py'
PYTHON = os.environ.get('SAS_MOTION_TEST_PYTHON') or (shutil.which('python3') if bpy else sys.executable)
SUCCESS_RESPONSE = b'{"schema":"sas.inbetween.response","version":1,"request_id":"fixture-a","samples":[]}'


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
        with patch.object(subprocess, 'Popen', side_effect=OSError('synthetic spawn failure')):
            with self.assertRaisesRegex(OSError, 'synthetic spawn failure'):
                self.start('pass')
        self.assertEqual(list(self.root.glob('sas-mlp-*')), [])

    def test_directory_arguments_and_environment(self):
        with patch.dict(os.environ, {key: 'not forwarded' for key in
                                     ('SAS_SECRET', 'PYTHONPATH', 'HTTPS_PROXY', 'BLENDER_USER_RESOURCES',
                                      'GODOT_PROJECT_PATH')}):
            job = self.start('''
import json, os, sys
from pathlib import Path
assert set(os.listdir()) == {'request.json', 'stderr.log'}
assert not {'SAS_SECRET', 'PYTHONPATH', 'HTTPS_PROXY', 'BLENDER_USER_RESOURCES',
            'GODOT_PROJECT_PATH'} & os.environ.keys()
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

    def test_profiles_are_explicit_without_freeform_command_or_fallback(self):
        worker_dir = self.root / 'nom avec espaces & point-virgule;'
        worker_dir.mkdir()
        worker = worker_dir / 'motion_fixture_worker.py'
        worker.write_text(f'''
from pathlib import Path
import json, sys
assert sys.argv[1:] == []
assert sys.stdin.read() == ''
req = json.loads(Path('request.json').read_text(encoding='utf-8'))
assert req['joints'][0]['id'] == 'racine\\u00e9'
result = json.loads({SUCCESS_RESPONSE!r})
result['samples'] = [dict(req['anchors'][0], frame=f) for f in req['sample_frames']]
Path('response.json').write_text(json.dumps(result), encoding='utf-8')
''', encoding='utf-8')
        fixture = profiles.FIXTURE_PROFILE
        for engine, weights, diagnostic in (
            (fixture, self.weights, 'ne reçoit pas de poids'),
            (profiles.DEFAULT_PROFILE, self.weights, 'sprint4_worker.py'),
            ('shell', None, 'Profil de mouvement inconnu'),
        ):
            with self.subTest(engine=engine), self.assertRaisesRegex(MotionProcessError, diagnostic):
                MotionJob(PYTHON, worker, weights, request(), profile=engine, temp_root=self.root)
        self.assertEqual(list(self.root.glob('sas-mlp-*')), [])
        payload = request()
        payload['joints'][0]['id'] = 'racineé'
        job = MotionJob(PYTHON, worker, None, payload, profile=fixture, temp_root=self.root)
        self.addCleanup(self.close, job)
        wait_for(lambda: job.poll() is not None)
        self.assertEqual([row['frame'] for row in job.result()['samples']], payload['sample_frames'])

    def test_reject_outbound_formats_and_sizes_before_spawning(self):
        for changed, diagnostic in (
            ({'version': True}, 'Requête de mouvement'),
            ({'request_id': 'a'*129}, 'Requête de mouvement'),
            ({'joints': 'not a list'}, 'Requête de mouvement'),
            ({'joints': [dict(request()['joints'][0], project='/private')]}, 'articulation v1 invalide'),
            ({'fps': [0, 1]}, 'cadence v1 invalide'),
            ({'sample_frames': [True]}, 'image v1 invalide'),
            ({'contacts': [dict(joint='root', kind='pose', samples=[{'project': '/private'}])]},
             'plafond de contacts'),
            ({'anchors': [float('inf')]}, 'non fini'),
            ({'anchors': [10**129]}, 'entier JSON trop long'),
            ({'anchors': [{1: 'key'}]}, 'clé JSON non textuelle'),
            ({'anchors': ['\ud800']}, 'Requête MLP invalide'),
            ({'anchors': ['é'] * JSON_LIMIT}, 'plafond d’ancres'),
            ({'anchors': ['é' * (JSON_LIMIT//2)]}, 'trop volumineux'),
            ({'anchors': [json.loads('['*40 + '0' + ']'*40)]}, 'trop profond'),
        ):
            with self.subTest(fields=changed.keys(), diagnostic=diagnostic):
                with self.assertRaisesRegex(MotionProcessError, diagnostic):
                    self.start('raise AssertionError("must not launch")', payload=dict(request(), **changed))
        for timeout in (0, -1, 3601, float('nan'), float('inf'), '1', True):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(MotionProcessError, 'Délai MLP invalide'):
                self.start('raise AssertionError("must not launch")', timeout=timeout)
        self.assertEqual(list(self.root.glob('sas-mlp-*')), [])

    def test_humanoid_fixture_exchanges_requests_and_responses_over_64_kib(self):
        profile = profiles.FIXTURE_PROFILE
        config = profiles.PROFILES[profile]
        self.assertEqual((config.request_bytes, config.response_bytes), (8 * 1024**2, 5 * 1024**2))
        worker = self.root / config.worker_name
        worker.write_text('''
import json
from pathlib import Path
req = json.loads(Path('request.json').read_text(encoding='utf-8'))
payload = dict(schema='sas.inbetween.response', version=1, request_id=req['request_id'],
               samples=[dict(req['anchors'][0], frame=f) for f in req['sample_frames']])
Path('response.json').write_text(json.dumps(payload), encoding='utf-8')
''', encoding='utf-8')
        payload = request()
        payload['joints'] = [dict(id=f'joint-{i}', parent=i-1 if i else None,
                                  rest_translation=[0, 0, 0], rest_rotation=[1, 0, 0, 0])
                             for i in range(config.max_joints)]
        translations = [[.12345678901234567] * 3] * config.max_joints
        rotations = [[1, 0, 0, 0]] * config.max_joints
        payload['anchors'] = [dict(frame=i*2, translations=translations, rotations=rotations) for i in range(32)]
        payload['sample_frames'] = list(range(1, 63, 2))
        job = MotionJob(PYTHON, worker, None, payload, profile=profile, temp_root=self.root)
        self.addCleanup(self.close, job)
        wait_for(lambda: job.poll() is not None)
        for filename in ('request.json', 'response.json'):
            self.assertGreater((job.directory / filename).stat().st_size, JSON_LIMIT)
        self.assertEqual(len(job.result()['samples']), 31)

    def test_humanoid_fixture_bounds_dimensions_and_independent_byte_budgets(self):
        profile = profiles.FIXTURE_PROFILE
        config = profiles.PROFILES[profile]
        legacy = profiles.PROFILES[profiles.DEFAULT_PROFILE]
        self.assertEqual((legacy.request_bytes, legacy.response_bytes), (JSON_LIMIT, JSON_LIMIT))
        worker = self.root / config.worker_name
        contact = dict(joint='root', kind='pose', samples=[])
        worker.write_text('raise AssertionError("must not launch")', encoding='utf-8')
        for changed, diagnostic in (
            ({'joints': request()['joints'] * (config.max_joints//3+1)}, 'plafond d’articulations'),
            ({'anchors': [None] * (config.max_anchors+1)}, 'plafond d’ancres'),
            ({'sample_frames': list(range(config.max_samples+1))}, 'plafond d’images'),
            ({'contacts': [contact] * (config.max_contacts+1)}, 'plafond de contacts'),
            ({'contacts': [dict(contact, samples=[None] * (config.max_contact_samples+1))]},
             'plafond d’échantillons'),
            ({'contacts': [dict(contact, samples=[{'project': '/private'}])]}, 'échantillon de contact v1 invalide'),
            ({'joints': [dict(request()['joints'][0], id='é' * (config.request_bytes//2))]},
             'trop volumineux'),
        ):
            with self.subTest(diagnostic=diagnostic), self.assertRaisesRegex(MotionProcessError, diagnostic):
                MotionJob(PYTHON, worker, None, dict(request(), **changed), profile=profile, temp_root=self.root)
        with self.assertRaisesRegex(MotionProcessError, 'maximum 120 s'):
            MotionJob(PYTHON, worker, None, request(), profile=profile, temp_root=self.root, timeout=121)
        self.assertEqual(list(self.root.glob('sas-mlp-*')), [])
        for extra in (0, 1):
            worker.write_text(f'from pathlib import Path\n'
                              f'Path("response.json").write_bytes({SUCCESS_RESPONSE!r}'
                              f'.ljust({config.response_bytes+extra}, b" "))\n', encoding='utf-8')
            job = MotionJob(PYTHON, worker, None, dict(request(), sample_frames=[]),
                            profile=profile, temp_root=self.root)
            self.addCleanup(self.close, job)
            wait_for(lambda: job.poll() is not None)
            if extra:
                with self.assertRaisesRegex(MotionProcessError, 'réponse trop volumineuse'):
                    job.result()
            else:
                self.assertEqual(job.result()['samples'], [])
            self.close(job)
        too_many_samples = dict(json.loads(SUCCESS_RESPONSE), samples=[None] * (config.max_samples+1))
        worker.write_text(f'from pathlib import Path\n'
                          f'Path("response.json").write_bytes({json.dumps(too_many_samples).encode()!r})\n',
                          encoding='utf-8')
        job = MotionJob(PYTHON, worker, None, request(), profile=profile, temp_root=self.root)
        self.addCleanup(self.close, job)
        wait_for(lambda: job.poll() is not None)
        with self.assertRaisesRegex(MotionProcessError, 'plafond d’images'):
            job.result()

    def test_validate_motion_response_is_strict_and_rig_independent(self):
        payload = dict(request(), sample_frames=[2, 3])
        first = dict(payload['anchors'][0], frame=2)
        second = dict(first, frame=3)
        response = dict(json.loads(SUCCESS_RESPONSE), samples=[first, second])
        self.assertIs(process.validate_motion_response(response, payload), response)
        for rows, diagnostic in (
            ([first], 'nombre de poses'),
            ([second, first], 'mal ordonnée'),
            ([first, first], 'mal ordonnée'),
            ([dict(first, translations=[[0, 0, 0]]), second], 'dimensions'),
            ([dict(first, translations=[[0, 0, float('nan')]]*3), second], 'translation'),
            ([dict(first, rotations=[[1, 0, 0]]*3), second], 'quaternion'),
            ([dict(first, rotations=[[1, 1, 0, 0]]*3), second], 'quaternion'),
        ):
            with self.subTest(diagnostic=diagnostic), self.assertRaisesRegex(MotionProcessError, diagnostic):
                process.validate_motion_response(dict(response, samples=rows), payload)

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
        wait_for(lambda: job.stop_reason is not None)  # No modal poll needed for enforcement.
        wait_for(lambda: job.poll() is not None)
        with self.assertRaisesRegex(MotionProcessError, 'Délai.*dépassé'):
            job.result()

    @unittest.skipUnless(sys.platform == 'linux' and Path('/proc/self/stat').is_file(), 'Linux /proc requis')
    def test_descendants_die_on_cancel_timeout_close_and_normal_exit(self):
        grandchild = 'import time; time.sleep(60)'
        child = f'''
import subprocess, sys, time
from pathlib import Path
process = subprocess.Popen([sys.executable, '-c', {grandchild!r}])
Path('grandchild.tmp').write_text(str(process.pid))
Path('grandchild.tmp').rename('grandchild.pid')
time.sleep(60)
'''
        leader = f'''
import subprocess, sys, time
from pathlib import Path
process = subprocess.Popen([sys.executable, '-c', {child!r}])
Path('child.tmp').write_text(str(process.pid))
Path('child.tmp').rename('child.pid')
while not Path('exit').is_file():
    time.sleep(.01)
Path('response.json').write_bytes({SUCCESS_RESPONSE!r})
'''

        def stopped(pid):
            try:
                # Remainder after comm (which can have spaces/parentheses).
                return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] in {'X', 'Z'}
            except (FileNotFoundError, ProcessLookupError):
                return True

        for mode in ('cancel', 'timeout', 'close', 'normal'):
            with self.subTest(mode=mode):
                job = self.start(leader, timeout=2 if mode == 'timeout' else 60)
                wait_for(lambda: all((job.directory / f'{name}.pid').exists() for name in ('child', 'grandchild')))
                children = [int((job.directory / f'{name}.pid').read_text()) for name in ('child', 'grandchild')]
                self.assertFalse(any(stopped(pid) for pid in children))
                if mode == 'cancel':
                    job.cancel()
                elif mode == 'close':
                    self.close(job)
                elif mode == 'normal':
                    (job.directory / 'exit').touch()
                # No poll for termination; also proves timeout still fires with no timer ticks.
                wait_for(lambda: all(stopped(pid) for pid in children))
                if mode != 'close':
                    wait_for(lambda: job.poll() is not None)
                    if mode == 'normal':
                        self.assertEqual(job.result()['samples'], [])
                    else:
                        with self.assertRaisesRegex(MotionProcessError, 'annulée|Délai.*dépassé'):
                            job.result()
                self.close(job)

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
        for value in (None, b'{', b'\xff', b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e400}', b'x' * (JSON_LIMIT+1),
                      SUCCESS_RESPONSE.replace(b'[]', b'['*40 + b'0' + b']'*40),
                      SUCCESS_RESPONSE.replace(b'[]', b'["\\ud800"]'),
                      SUCCESS_RESPONSE.replace(b'[]', b'[' + b'1'*129 + b']'),
                      SUCCESS_RESPONSE.replace(b'"version":1', b'"version":true'),
                      SUCCESS_RESPONSE.replace(b'"version":1', b'"version":2'),
                      SUCCESS_RESPONSE.replace(b'fixture-a', b'wrong'),
                      SUCCESS_RESPONSE.replace(b'[]', b'{}')):
            with self.subTest(prefix=None if value is None else value[:80]):
                job = self.start('pass' if value is None else
                                 f'from pathlib import Path\nPath("response.json").write_bytes({value!r})')
                wait_for(lambda: job.poll() is not None)
                with self.assertRaisesRegex(MotionProcessError, 'Réponse MLP invalide'):
                    job.result()
                self.close(job)

    @unittest.skipUnless(os.name == 'posix', 'Symlinks et FIFO POSIX requis')
    def test_response_symlink_fifo_and_directory_are_rejected(self):
        for create in ("os.symlink('other.json', 'response.json')",
                       "os.mkfifo('response.json')", "os.mkdir('response.json')"):
            with self.subTest(create=create):
                job = self.start(f'import os\nfrom pathlib import Path\n'
                                 f'Path("other.json").write_bytes({SUCCESS_RESPONSE!r})\n{create}')
                wait_for(lambda: job.poll() is not None)
                with self.assertRaisesRegex(MotionProcessError, 'fichier de réponse non ordinaire'):
                    job.result()
                self.close(job)

    def test_response_and_stderr_byte_boundaries_even_on_fast_exit(self):
        for stderr_extra, response_extra in ((0, 0), (1, 0), (0, 1)):
            with self.subTest(stderr_extra=stderr_extra, response_extra=response_extra):
                job = self.start(f'''
from pathlib import Path
import sys
Path("response.json").write_bytes({SUCCESS_RESPONSE!r}.ljust({JSON_LIMIT + response_extra}, b' '))
sys.stderr.buffer.write(b'x' * {process.LOG_LIMIT + stderr_extra})
''')
                wait_for(lambda: job.poll() is not None)
                self.assertEqual((job.directory / 'stderr.log').stat().st_size, process.LOG_LIMIT)
                if stderr_extra or response_extra:
                    with self.assertRaisesRegex(MotionProcessError, 'trop volumineu'):
                        job.result()
                else:
                    self.assertEqual(job.result()['samples'], [])
                self.close(job)

    def test_live_stderr_flood_is_stopped_and_disk_log_stays_capped(self):
        job = self.start('''
import os
chunk = b'x' * 65536
while True:
    os.write(2, chunk)
''')
        wait_for(lambda: job.stop_reason is not None)
        wait_for(lambda: job.poll() is not None)
        self.assertEqual((job.directory / 'stderr.log').stat().st_size, process.LOG_LIMIT)
        with self.assertRaisesRegex(MotionProcessError, 'Journal MLP trop volumineux'):
            job.result()

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
