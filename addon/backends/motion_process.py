# SPDX-License-Identifier: GPL-3.0-or-later
"""Launch the trusted, single-process Sprint 4 NumPy worker. No bpy or NumPy here."""

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


CHECKPOINT_BYTES = 61_080  # Sprint 4 raw little-endian float32 checkpoint
JSON_LIMIT = 64 * 1024
LOG_LIMIT = 1024 * 1024


class MotionProcessError(RuntimeError):
    """External motion job failed or was cancelled; no result may be applied."""


def _local_file(value, label):
    if not value or not Path(value).is_absolute():
        raise MotionProcessError(f'{label} : choisir un chemin absolu explicite.')
    path = Path(value)
    try:
        info = path.stat()
    except OSError as exc:
        raise MotionProcessError(f'{label} : fichier absent ou inaccessible.') from exc
    if not stat.S_ISREG(info.st_mode) or not os.access(path, os.R_OK):
        raise MotionProcessError(f'{label} : fichier ordinaire lisible requis.')
    return path, info


def check_weights(value):
    """Check file availability/size only; only the external worker loads the values."""
    path, info = _local_file(value, 'Poids MLP')
    if info.st_size != CHECKPOINT_BYTES:
        raise MotionProcessError(f'Poids MLP : taille incorrecte ({CHECKPOINT_BYTES} octets attendus).')
    return path


def motion_command(python_path, worker_path, weights_path, *, device='cpu'):
    if device != 'cpu':
        raise MotionProcessError('Périphérique incompatible : le MLP Sprint 4 utilise uniquement le CPU.')
    weights = check_weights(weights_path)
    python, _ = _local_file(python_path, 'Python externe')
    if not os.access(python, os.X_OK):
        raise MotionProcessError('Python externe non exécutable.')
    worker, _ = _local_file(worker_path, 'Worker Sprint 4')
    if worker.name != 'sprint4_worker.py':
        raise MotionProcessError('Choisir le script de confiance sprint4_worker.py.')
    # Keep the original Python path: resolving a symlink can bypass its venv.
    return [str(python), '-E', '-s', '-B', str(worker), '--weights', str(weights)]


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


def _read_response(path, request_id):
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('fichier de réponse non ordinaire')
        with path.open('rb') as stream:
            data = stream.read(JSON_LIMIT + 1)
        if len(data) > JSON_LIMIT:
            raise ValueError('réponse trop volumineuse')
        response = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object,
                              parse_float=_finite_float, parse_constant=_finite_float)
        if (type(response) is not dict or response.keys() != {'schema', 'version', 'request_id', 'samples'}
                or response['schema'] != 'sas.inbetween.response' or type(response['version']) is not int
                or response['version'] != 1 or response['request_id'] != request_id
                or type(response['samples']) is not list):
            raise ValueError('en-tête invalide ou autre job')
        return response  # Rig/profile/sample validation belongs to bake_mlp.
    except (OSError, ValueError, RecursionError) as exc:
        raise MotionProcessError(f'Réponse MLP invalide : {exc}') from exc


class MotionJob:
    """One job in a fresh directory; call poll/cancel/result/close from one thread.

    The selected worker is trusted and does not spawn child processes. This is
    isolation of the exchange, not a sandbox for arbitrary Python scripts.
    """

    def __init__(self, python_path, worker_path, weights_path, request, *, device='cpu', timeout=60,
                 temp_root=None):
        command = motion_command(python_path, worker_path, weights_path, device=device)
        if not math.isfinite(timeout) or timeout <= 0:
            raise MotionProcessError('Délai MLP invalide.')
        if (type(request) is not dict or request.keys() != {
                'schema', 'version', 'request_id', 'fps', 'joints', 'anchors', 'sample_frames', 'contacts'}
                or request['schema'] != 'sas.inbetween.request' or type(request['version']) is not int
                or request['version'] != 1 or type(request['request_id']) is not str
                or not 0 < len(request['request_id']) <= 128):
            raise MotionProcessError('Requête de mouvement v1 requise.')
        try:
            data = json.dumps(request, allow_nan=False, separators=(',', ':')).encode('utf-8')
        except (TypeError, ValueError, RecursionError) as exc:
            raise MotionProcessError(f'Requête MLP invalide : {exc}') from exc
        if len(data) > JSON_LIMIT:
            raise MotionProcessError('Requête MLP trop volumineuse.')
        self._request_id = request['request_id']
        self._stop_reason = None
        self._closed = False
        self._exit = None
        self.directory = Path(tempfile.mkdtemp(prefix='sas-mlp-', dir=temp_root)).resolve()
        self._log = self.directory / 'stderr.log'
        try:
            (self.directory / 'request.json').write_bytes(data)
            with self._log.open('xb') as log:
                self._deadline = time.monotonic() + timeout
                self._process = subprocess.Popen(
                    command, cwd=self.directory, env=_environment(self.directory),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    start_new_session=os.name == 'posix',
                )
        except BaseException:
            shutil.rmtree(self.directory)
            raise

    def cancel(self, reason='Génération MLP annulée.'):
        """Invalidate even a completed response; do not wait for the worker here."""
        if not self._closed and self._stop_reason is None:
            self._stop_reason = reason
            if self._process.poll() is None:
                self._process.kill()

    @property
    def stop_reason(self):
        return self._stop_reason

    def poll(self):
        """None until exit; remember deadlines and cancellation independently of exit code."""
        if self._closed:
            raise MotionProcessError('Job MLP fermé.')
        if self._exit is None:
            if self._stop_reason is None and time.monotonic() >= self._deadline:
                self.cancel('Délai MLP dépassé.')
            if self._stop_reason is None and self._log.stat().st_size > LOG_LIMIT:
                self.cancel('Journal MLP trop volumineux.')
            self._exit = self._process.poll()
        return self._exit

    def result(self):
        status = self.poll()
        if self._stop_reason is not None:
            raise MotionProcessError(self._stop_reason)
        if status is None:
            raise MotionProcessError('Le worker MLP tourne encore.')
        if status != 0:
            with self._log.open('rb') as stream:
                stream.seek(max(0, self._log.stat().st_size - 4096))
                tail = stream.read(4096).decode('utf-8', errors='replace')
            lines = [line.strip() for line in tail.splitlines() if line.strip()]
            detail = lines[-1][-300:] if lines else 'aucun diagnostic'
            raise MotionProcessError(f'Échec du worker MLP (code {status}) : {detail}')
        return _read_response(self.directory / 'response.json', self._request_id)

    def close(self):
        """Idempotent; allow a short reap on shutdown, never an unbounded UI wait."""
        if self._closed:
            return
        self.cancel()
        self._closed = True
        try:
            self._process.wait(timeout=.05)
        except subprocess.TimeoutExpired:
            threading.Thread(target=self._reap, name='sas-mlp-cleanup', daemon=True).start()
        else:
            shutil.rmtree(self.directory)

    def _reap(self):
        try:
            self._process.wait()
            shutil.rmtree(self.directory)
        except OSError:
            logging.getLogger(__name__).exception('Échec du nettoyage du job MLP')
