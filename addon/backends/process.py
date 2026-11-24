# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-blocking POSIX process lifecycle; no Blender dependency."""

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

from .rig_exchange import read_json, write_json


STAGES = ("Checking external environment / CUDA", "Extracting source mesh", "Predicting skeleton",
          "Extracting predicted skeleton", "Predicting skin weights", "Merging onto source geometry",
          "Decoding and validating result")
LOG_LIMIT = 8 * 1024 * 1024


def unirig_command(python, checkout):
    if sys.platform != "linux":
        raise ValueError("This experimental launcher currently supports Linux only.")
    if not python or not checkout:
        raise ValueError("Set the external Python executable and UniRig checkout first.")
    python, checkout = (Path(value).expanduser() for value in (python, checkout))
    if not python.is_absolute() or not checkout.is_absolute():
        raise ValueError("Use absolute paths to the external Python and UniRig checkout.")
    # Do not resolve the Python symlink: doing so can bypass its virtualenv.
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("The configured external Python is not executable.")
    if not all((checkout / path).is_file() for path in (
        "run.py", "src/data/extract.py", "src/inference/merge.py",
        "configs/task/quick_inference_skeleton_articulationxl_ar_256.yaml",
        "configs/task/quick_inference_unirig_skin.yaml",
    )):
        raise ValueError("Invalid UniRig checkout; see the pinned revision in the backend guide.")
    for path in ("skeleton/articulation-xl_quantization_256", "skin/articulation-xl"):
        if not (checkout / "experiments" / path / "model.ckpt").is_file():
            raise ValueError(f"Missing local UniRig checkpoint: experiments/{path}/model.ckpt")
    return [str(python), "-E", "-s", "-B", "-u", str(Path(__file__).with_name("unirig_worker.py").resolve()),
            str(checkout.resolve())]


def _environment(directory):
    # Do not propagate arbitrary project variables, PYTHONPATH, API tokens or proxies.
    allow = ("PATH", "HOME", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES",
             "CUDA_HOME", "XDG_CACHE_HOME", "HF_HOME", "HF_HUB_CACHE", "TORCH_HOME")
    return {**{key: os.environ[key] for key in allow if key in os.environ},
            "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
            "TRANSFORMERS_OFFLINE": "1", "WANDB_MODE": "disabled", "TMPDIR": str(directory),
            "PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1", "BLENDER_USER_RESOURCES": str(directory / "profile")}


class ExternalJob:
    def __init__(self, command, request, *, timeout=1800, temp_root=None):
        self.directory = Path(tempfile.mkdtemp(prefix="sas-unirig-", dir=temp_root)).resolve()
        self._log = self.directory / "backend.log"
        self._stop_reason = None
        self._closed = False
        self._deadline = time.monotonic() + timeout
        try:
            write_json(self.directory / "request.json", request)
            with self._log.open("wb") as log:
                self._process = subprocess.Popen(command, cwd=self.directory, stdin=subprocess.DEVNULL,
                                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                                 env=_environment(self.directory))
        except BaseException:
            shutil.rmtree(self.directory)
            raise

    def progress(self):
        try:
            with (self.directory / "progress").open("rb") as stream:
                text = stream.read(2)
            if text in tuple(str(i).encode("ascii") for i in range(len(STAGES))):
                return int(text)
        except FileNotFoundError:
            pass
        return 0

    def _kill_group(self):
        try:
            os.killpg(self._process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def cancel(self, reason="UniRig cancelled."):
        if not self._closed and self._stop_reason is None:
            self._stop_reason = reason
            self._kill_group()

    def poll(self):
        """None while running; otherwise exit code. No wait or pipe drain."""
        status = self._process.poll()
        if status is None and self._stop_reason is None:
            if time.monotonic() >= self._deadline:
                self.cancel("UniRig timed out.")
            elif self._log.stat().st_size > LOG_LIMIT:
                self.cancel("UniRig exceeded its log size limit.")
        return status

    def result(self):
        status = self.poll()
        if status is None:
            raise RuntimeError("UniRig is still running.")
        if self._stop_reason:
            raise RuntimeError(self._stop_reason)
        if status != 0:
            with self._log.open("rb") as stream:
                stream.seek(max(0, self._log.stat().st_size - 2048))
                tail = stream.read(2048).decode("utf-8", errors="replace")
            lines = [line.strip() for line in tail.splitlines() if line.strip()]
            detail = lines[-1].replace(str(self.directory), "<job>")[-220:] if lines else "No diagnostic output."
            raise RuntimeError(f"{STAGES[self.progress()]} failed (exit {status}): {detail}")
        return read_json(self.directory / "response.json")

    def close(self):
        """Kill descendants before cleanup. Reap off-thread if not yet exited."""
        if self._closed:
            return
        self._closed = True
        self._kill_group()
        if self._process.poll() is not None:
            shutil.rmtree(self.directory)
        else:
            threading.Thread(target=self._reap, daemon=True).start()

    def _reap(self):
        self._process.wait()
        shutil.rmtree(self.directory)
