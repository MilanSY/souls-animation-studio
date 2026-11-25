# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded JSON exchange with explicitly configured, trusted local motion workers."""

import json
import logging
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import threading
import time

from .motion_profiles import CHECKPOINT_BYTES, DEFAULT_PROFILE, LEGACY_JSON_LIMIT, PROFILES
from .motion_tree import MotionTree


JSON_LIMIT = LEGACY_JSON_LIMIT  # Legacy caller/test alias, not the transport ceiling.
LOG_LIMIT = 1024 * 1024
_JSON_DEPTH = 32
_JSON_INTEGER_LIMIT = 10**128
_LOG_TAIL = 4096
_REQUEST_FIELDS = {'schema', 'version', 'request_id', 'fps', 'joints', 'anchors', 'sample_frames', 'contacts'}
_RESPONSE_FIELDS = {'schema', 'version', 'request_id', 'samples'}
_JOINT_FIELDS = {'id', 'parent', 'rest_translation', 'rest_rotation'}
_POSE_FIELDS = {'frame', 'translations', 'rotations'}
_CONTACT_FIELDS = {'joint', 'kind', 'samples'}
_CONTACT_SAMPLE_FIELDS = {'frame', 'position', 'rotation', 'position_tolerance', 'angle_tolerance'}


class MotionProcessError(RuntimeError):
    """External motion job failed or was cancelled; no result may be applied."""


def _local_file(value, label):
    if not isinstance(value, (str, os.PathLike)) or not value or not Path(value).is_absolute():
        raise MotionProcessError(f'{label} : choisir un chemin absolu explicite.')
    path = Path(value)
    try:
        info = path.stat()
    except (OSError, ValueError) as exc:
        raise MotionProcessError(f'{label} : fichier absent ou inaccessible.') from exc
    if not stat.S_ISREG(info.st_mode) or not os.access(path, os.R_OK):
        raise MotionProcessError(f'{label} : fichier ordinaire lisible requis.')
    return path, info


def check_weights(value):
    """Legacy Sprint 4 preflight; only the external worker loads the values."""
    return _check_weights(value, CHECKPOINT_BYTES)


def _check_weights(value, expected_bytes):
    path, info = _local_file(value, 'Poids MLP')
    if info.st_size != expected_bytes:
        raise MotionProcessError(f'Poids MLP : taille incorrecte ({expected_bytes} octets attendus).')
    return path


def motion_command(python_path, worker_path, weights_path, *, device='cpu', profile=DEFAULT_PROFILE):
    """Build fixed argv from a built-in profile; no free-form command/flags."""
    if type(profile) is not str or profile not in PROFILES:
        raise MotionProcessError('Profil de mouvement inconnu.')
    config = PROFILES[profile]
    if device not in config.devices:
        raise MotionProcessError(f'Périphérique incompatible avec le profil {profile}.')
    args = []
    if config.weights_bytes is not None:
        weights = _check_weights(weights_path, config.weights_bytes)
        args = ['--weights', str(weights)]
    elif weights_path is not None:
        raise MotionProcessError('Ce profil de mouvement ne reçoit pas de poids.')
    python, _ = _local_file(python_path, 'Python externe')
    if not os.access(python, os.X_OK) or (os.name == 'nt' and python.suffix.lower() != '.exe'):
        raise MotionProcessError('Python externe non exécutable.')
    worker, _ = _local_file(worker_path, 'Worker externe')
    if worker.name != config.worker_name:
        raise MotionProcessError(f'Choisir le script de confiance {config.worker_name}.')
    # Preserve the original Python path: resolving a symlink can bypass its venv.
    return [str(python), '-E', '-s', '-B', str(worker), *args]


def _environment(directory):
    # No inherited project paths, Blender variables, tokens, proxies or PYTHONPATH.
    keep = ('SYSTEMROOT', 'WINDIR')  # Windows Python/DLL bootstrap
    return {**{key: os.environ[key] for key in keep if key in os.environ},
            'TMPDIR': str(directory), 'TMP': str(directory), 'TEMP': str(directory),
            'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('champ JSON dupliqué')
    return result


def _finite_float(text):
    number = float(text)
    if not math.isfinite(number):
        raise ValueError('nombre JSON non fini')
    return number


def _bounded_int(text):
    if len(text.removeprefix('-')) > 128:
        raise ValueError('entier JSON trop long')
    return int(text)


def _validate_json(value, byte_limit):
    # Bound traversal before encoding too (e.g. shared or cyclic containers).
    remaining = byte_limit

    def visit(item, depth):
        nonlocal remaining
        remaining -= 1
        if remaining < 0:
            raise ValueError('document JSON trop volumineux')
        if depth > _JSON_DEPTH:
            raise ValueError('document JSON trop profond')
        kind = type(item)
        if kind is dict:
            for key, entry in item.items():
                if type(key) is not str:
                    raise ValueError('clé JSON non textuelle')
                visit(key, depth + 1)
                visit(entry, depth + 1)
        elif kind in (list, tuple):
            for entry in item:
                visit(entry, depth + 1)
        elif kind is str:
            remaining -= len(item)
            if remaining < 0:
                raise ValueError('document JSON trop volumineux')
            item.encode('utf-8')  # reject unpaired surrogates in either direction
        elif kind is int:
            if not -_JSON_INTEGER_LIMIT < item < _JSON_INTEGER_LIMIT:
                raise ValueError('entier JSON trop long')
        elif kind is float:
            if not math.isfinite(item):
                raise ValueError('nombre JSON non fini')
        elif item is not None and kind is not bool:
            raise ValueError('valeur non représentable en JSON')

    visit(value, 0)


def _envelope(value, kind, fields, exchange_format='sas.inbetween', version=1):
    return (type(value) is dict and value.keys() == fields and type(value['schema']) is str
            and value['schema'] == f'{exchange_format}.{kind}'
            and type(value['version']) is int and value['version'] == version
            and type(value['request_id']) is str and 0 < len(value['request_id']) <= 128)


def _check_request_fields(request, config):
    # Request shape only; rig topology, frame coverage and unit rotations are
    # enforced by the profile/adapter. Do not forward unrelated nested fields.
    arrays = (list, tuple)
    numbers = (float, int)

    def obj(value, fields):
        return type(value) is dict and value.keys() == fields

    def vector(value, size):
        return type(value) in arrays and len(value) == size and all(type(v) in numbers for v in value)

    fps = request['fps']
    if len(fps) != 2 or any(type(v) is not int or v <= 0 for v in fps):
        raise ValueError('cadence v1 invalide')
    for joint in request['joints']:
        if (not obj(joint, _JOINT_FIELDS) or type(joint['id']) is not str or not joint['id']
                or not (joint['parent'] is None or type(joint['parent']) is int)
                or not vector(joint['rest_translation'], 3) or not vector(joint['rest_rotation'], 4)):
            raise ValueError('articulation v1 invalide')
    for pose in request['anchors']:
        if (not obj(pose, _POSE_FIELDS) or type(pose['frame']) not in numbers
                or any(type(pose[key]) not in arrays or len(pose[key]) != len(request['joints'])
                       or not all(vector(v, size) for v in pose[key])
                       for key, size in (('translations', 3), ('rotations', 4)))):
            raise ValueError('ancre v1 invalide')
    frames = request['sample_frames']
    if any(type(frame) is not int for frame in frames) or any(a >= b for a, b in zip(frames, frames[1:])):
        raise ValueError('image v1 invalide')
    for contact in request['contacts']:
        if (not obj(contact, _CONTACT_FIELDS) or type(contact['joint']) is not str or not contact['joint']
                or contact['kind'] not in config.contact_kinds or type(contact['samples']) not in arrays):
            raise ValueError('contact v1 invalide')
        if len(contact['samples']) > config.max_contact_samples:
            raise ValueError('plafond d’échantillons par contact dépassé')
        for sample in contact['samples']:
            if (not obj(sample, _CONTACT_SAMPLE_FIELDS) or type(sample['frame']) not in numbers
                    or not vector(sample['position'], 3) or type(sample['position_tolerance']) not in numbers):
                raise ValueError('échantillon de contact v1 invalide')
            if contact['kind'] == 'position':
                if sample['rotation'] is not None or sample['angle_tolerance'] is not None:
                    raise ValueError('contact de position v1 invalide')
            elif not vector(sample['rotation'], 4) or type(sample['angle_tolerance']) not in numbers:
                raise ValueError('contact de pose v1 invalide')


def _request_bytes(request, config):
    if (not _envelope(request, 'request', _REQUEST_FIELDS, config.exchange_format, config.exchange_version)
            or any(type(request[key]) not in (list, tuple)
                   for key in ('fps', 'joints', 'anchors', 'sample_frames', 'contacts'))):
        raise MotionProcessError('Requête de mouvement v1 requise.')
    try:
        for key, limit, label in (('joints', config.max_joints, 'd’articulations'),
                                  ('anchors', config.max_anchors, 'd’ancres'),
                                  ('sample_frames', config.max_samples, 'd’images'),
                                  ('contacts', config.max_contacts, 'de contacts')):
            if len(request[key]) > limit:
                raise ValueError(f'plafond {label} dépassé')
        _validate_json(request, config.request_bytes)
        encoder = json.JSONEncoder(allow_nan=False, ensure_ascii=False, separators=(',', ':'))
        data = bytearray()
        for part in encoder.iterencode(request):
            chunk = part.encode('utf-8')
            if len(data) + len(chunk) > config.request_bytes:
                raise ValueError('document JSON trop volumineux')
            data.extend(chunk)
        _check_request_fields(request, config)
        return data
    except (TypeError, ValueError, RecursionError) as exc:
        label = 'MLP' if config is PROFILES[DEFAULT_PROFILE] else 'de mouvement'
        raise MotionProcessError(f'Requête {label} invalide : {exc}') from exc


def _read_response(path, request_id, config):
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('fichier de réponse non ordinaire')
        flags = (os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
                 | getattr(os, 'O_BINARY', 0))
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            current = os.fstat(stream.fileno())
            if not stat.S_ISREG(current.st_mode) or not os.path.samestat(before, current):
                raise ValueError('fichier de réponse remplacé')
            if current.st_size > config.response_bytes:
                raise ValueError('réponse trop volumineuse')
            data = stream.read(config.response_bytes + 1)
        if len(data) > config.response_bytes:
            raise ValueError('réponse trop volumineuse')
        response = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object,
                              parse_int=_bounded_int, parse_float=_finite_float, parse_constant=_finite_float)
        _validate_json(response, config.response_bytes)
        if (not _envelope(response, 'response', _RESPONSE_FIELDS, config.exchange_format, config.exchange_version)
                or response['request_id'] != request_id or type(response['samples']) is not list):
            raise ValueError('en-tête invalide ou autre job')
        if len(response['samples']) > config.max_samples:
            raise ValueError('plafond d’images dépassé')
        return response  # Rig/profile/sample validation belongs to the animation adapter.
    except (OSError, ValueError, RecursionError) as exc:
        label = 'MLP' if config is PROFILES[DEFAULT_PROFILE] else 'de mouvement'
        raise MotionProcessError(f'Réponse {label} invalide : {exc}') from exc


def _validate_samples(response, request_id, joints, frames):
    def fail(detail):
        raise MotionProcessError(f'Réponse de mouvement v1 invalide : {detail}.')

    def vector(value, size):
        try:
            return (type(value) is list and len(value) == size
                    and all(type(v) in (int, float) and math.isfinite(v) for v in value))
        except OverflowError:
            return False

    if not _envelope(response, 'response', _RESPONSE_FIELDS) or response['request_id'] != request_id:
        fail('en-tête ou identifiant inattendu')
    rows = response['samples']
    if type(rows) is not list or len(rows) != len(frames):
        fail('nombre de poses inattendu')
    for row, frame in zip(rows, frames):
        if (type(row) is not dict or row.keys() != _POSE_FIELDS
                or type(row['frame']) is not int or row['frame'] != frame):
            fail('image inattendue ou mal ordonnée')
        translations, rotations = row['translations'], row['rotations']
        if any(type(values) is not list or len(values) != joints for values in (translations, rotations)):
            fail(f'dimensions de la pose à l’image {frame}')
        if not all(vector(v, 3) for v in translations):
            fail(f'translation invalide à l’image {frame}')
        if not all(vector(q, 4) and abs(math.hypot(*q) - 1) <= 1e-6 for q in rotations):
            fail(f'quaternion invalide à l’image {frame}')
    return response


def validate_motion_response(response, request):
    """Validate decoded v1 sample geometry against a known request, without bpy.

    Return response unchanged or raise MotionProcessError. This does not decode
    bytes, qualify a rig, check contacts or verify a bake.
    """
    if (not _envelope(request, 'request', _REQUEST_FIELDS)
            or type(request['joints']) not in (list, tuple)
            or type(request['sample_frames']) not in (list, tuple)
            or any(type(f) is not int for f in request['sample_frames'])
            or any(a >= b for a, b in zip(request['sample_frames'], request['sample_frames'][1:]))):
        raise MotionProcessError('Requête de mouvement v1 invalide pour valider les poses.')
    return _validate_samples(response, request['request_id'], len(request['joints']), request['sample_frames'])


class MotionJob:
    """One trusted worker in a fresh directory, not a sandbox.

    Call poll/cancel/result/close from one thread. The supervisor independently
    enforces the deadline; pipe draining never writes past LOG_LIMIT. The response
    limit controls what is accepted/read, not the trusted worker's disk quota.
    """

    def __init__(self, python_path, worker_path, weights_path, request, *, device='cpu', timeout=60,
                 temp_root=None, profile=DEFAULT_PROFILE):
        command = motion_command(python_path, worker_path, weights_path, device=device, profile=profile)
        self._config = PROFILES[profile]
        self._label = 'MLP' if profile == DEFAULT_PROFILE else profile
        if type(timeout) not in (int, float) or not 0 < timeout <= self._config.max_timeout:
            raise MotionProcessError(f'Délai {self._label} invalide (maximum {self._config.max_timeout} s).')
        data = _request_bytes(request, self._config)
        self._request_id = request['request_id']
        self._joints = len(request['joints'])
        self._frames = tuple(request['sample_frames'])
        self._stop_reason = None
        self._closed = False
        self._exit = None
        self._tail = b''
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._done = threading.Event()
        prefix = 'sas-mlp-' if profile == DEFAULT_PROFILE else 'sas-motion-'
        self.directory = Path(tempfile.mkdtemp(prefix=prefix, dir=temp_root)).resolve()
        log = None
        try:
            (self.directory / 'request.json').write_bytes(data)
            log = (self.directory / 'stderr.log').open('xb')
            self._deadline = time.monotonic() + timeout
            self._tree = MotionTree(command, cwd=self.directory, env=_environment(self.directory),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self._reader = threading.Thread(target=self._drain, args=(log,), name='sas-motion-log', daemon=True)
            self._reader.start()
            threading.Thread(target=self._supervise, name='sas-motion-supervisor', daemon=True).start()
        except BaseException:
            if hasattr(self, '_tree'):
                self._tree.stop()
                self._tree.reap()
            if log is not None:
                if hasattr(self, '_reader') and self._reader.is_alive():
                    self._reader.join(timeout=.5)
                else:
                    log.close()
                    if hasattr(self, '_tree'):
                        self._tree.process.stderr.close()
            shutil.rmtree(self.directory)
            raise

    def _invalidate(self, reason):
        with self._lock:
            if self._stop_reason is None:
                self._stop_reason = reason
        self._wake.set()

    def _drain(self, log):
        try:
            with log, self._tree.process.stderr as pipe:
                remaining = LOG_LIMIT
                while chunk := pipe.read1(8192):
                    if len(chunk) > remaining:
                        self._invalidate(f'Journal {self._label} trop volumineux.')
                        log.write(chunk[:remaining])
                        return
                    log.write(chunk)
                    self._tail = (self._tail + chunk)[-_LOG_TAIL:]
                    remaining -= len(chunk)
        except OSError:
            self._invalidate(f'Lecture du journal {self._label} impossible.')

    def _supervise(self):
        try:
            try:
                while self._stop_reason is None:
                    remaining = self._deadline - time.monotonic()
                    if remaining <= 0:
                        self._invalidate(f'Délai {self._label} dépassé.')
                        break
                    if self._tree.exited():
                        break
                    self._wake.wait(min(.02, remaining))
            finally:
                # Also on exit: a child may still be running after its leader exits.
                self._tree.stop()
                self._exit = self._tree.reap()
                self._reader.join(timeout=.5)
                if self._reader.is_alive():
                    self._invalidate(f'Flux du worker {self._label} encore ouvert après son arrêt.')
        except OSError as exc:
            self._invalidate(f'Arrêt du worker {self._label} impossible : {exc}')
            if self._exit is None:
                self._exit = -1
        finally:
            self._done.set()

    def cancel(self, reason=None):
        """Invalidate even a completed response; do not wait for the worker here."""
        if not self._closed:
            self._invalidate(reason if isinstance(reason, str) and reason else f'Génération {self._label} annulée.')

    @property
    def stop_reason(self):
        return self._stop_reason

    def poll(self):
        """None until exit and stderr handling are complete; never blocks."""
        if self._closed:
            raise MotionProcessError(f'Job {self._label} fermé.')
        return self._exit if self._done.is_set() else None

    def result(self):
        status = self.poll()
        if self._stop_reason is not None:
            raise MotionProcessError(self._stop_reason)
        if status is None:
            raise MotionProcessError(f'Le worker {self._label} tourne encore.')
        if status != 0:
            tail = self._tail.decode('utf-8', errors='replace')
            lines = [line.strip() for line in tail.splitlines() if line.strip()]
            detail = lines[-1][-300:] if lines else 'aucun diagnostic'
            raise MotionProcessError(f'Échec du worker {self._label} (code {status}) : {detail}')
        response = _read_response(self.directory / 'response.json', self._request_id, self._config)
        if self._config.strict_samples:
            _validate_samples(response, self._request_id, self._joints, self._frames)
        return response

    def close(self):
        """Idempotent; allow a short reap on shutdown, never an unbounded UI wait."""
        if self._closed:
            return
        self.cancel()
        self._closed = True
        if self._done.wait(timeout=.05):
            shutil.rmtree(self.directory)
        else:
            threading.Thread(target=self._cleanup, name='sas-motion-cleanup', daemon=True).start()

    def _cleanup(self):
        self._done.wait()
        try:
            shutil.rmtree(self.directory)
        except OSError:
            logging.getLogger(__name__).exception('Échec du nettoyage du job %s', self._label)
