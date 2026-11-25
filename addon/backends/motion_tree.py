# SPDX-License-Identifier: GPL-3.0-or-later
"""Process tree ownership for trusted workers, not an OS security sandbox.

POSIX: workers inherit a new session/process group. Use waitid(WNOWAIT) so the
leader PID still reserves the group ID when killing descendants at exit.
Descendants that deliberately detach (setsid/setpgid) are outside this guarantee.

Windows: start a gated Python bootstrap, enroll it in a kill-on-close Job Object,
then let it launch the real worker. No worker code runs before enrollment. A host
job policy that forbids nested jobs causes a launch failure, not a PID-only
fallback. The bootstrap requires the same trusted Python as the worker.
"""

import os
import signal
import subprocess


_WINDOWS_GATE = (
    'import subprocess, sys\n'
    'if sys.stdin.buffer.read(1) != b"1": raise SystemExit(125)\n'
    'raise SystemExit(subprocess.call(sys.argv[1:], '
    'stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW))\n'
)


class MotionTree:
    def __init__(self, command, **options):
        self._job = None
        self._stopped = False
        if os.name == 'nt':
            self._job = _WindowsJob()
            try:
                # Prevent automatic site hooks before Job Object assignment.
                self.process = subprocess.Popen(
                    [command[0], '-I', '-S', '-B', '-c', _WINDOWS_GATE, *command],
                    stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW, **options)
                try:
                    with self.process.stdin as gate:
                        self._job.assign(self.process.pid)
                        gate.write(b'1')
                except BaseException:
                    self.process.kill()
                    self.process.wait()
                    self.process.stderr.close()
                    raise
            except BaseException:
                self._job.close()
                raise
        elif os.name == 'posix' and hasattr(os, 'waitid') and hasattr(os, 'WNOWAIT'):
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                            start_new_session=True, **options)
        else:
            raise OSError('Arrêt de l’arbre du worker indisponible sur cette plateforme.')

    def exited(self):
        if self._job is not None:
            return self.process.poll() is not None
        return os.waitid(os.P_PID, self.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None

    def stop(self):
        if self._stopped:
            return
        if self._job is not None:
            self._job.close()
        else:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._stopped = True

    def reap(self):
        return self.process.wait()


class _WindowsJob:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as t

        class BasicLimits(c.Structure):
            _fields_ = [('PerProcessUserTimeLimit', c.c_int64), ('PerJobUserTimeLimit', c.c_int64),
                        ('LimitFlags', t.DWORD), ('MinimumWorkingSetSize', c.c_size_t),
                        ('MaximumWorkingSetSize', c.c_size_t), ('ActiveProcessLimit', t.DWORD),
                        ('Affinity', c.c_size_t), ('PriorityClass', t.DWORD),
                        ('SchedulingClass', t.DWORD)]

        class ExtendedLimits(c.Structure):
            _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', c.c_uint64 * 6),
                        ('ProcessMemoryLimit', c.c_size_t), ('JobMemoryLimit', c.c_size_t),
                        ('PeakProcessMemoryUsed', c.c_size_t), ('PeakJobMemoryUsed', c.c_size_t)]

        api = c.WinDLL('kernel32', use_last_error=True)

        def bind(name, result, *args):
            function = getattr(api, name)
            function.restype, function.argtypes = result, args
            return function

        create = bind('CreateJobObjectW', t.HANDLE, c.c_void_p, t.LPCWSTR)
        set_info = bind('SetInformationJobObject', t.BOOL, t.HANDLE, c.c_int, c.c_void_p, t.DWORD)
        self._assign = bind('AssignProcessToJobObject', t.BOOL, t.HANDLE, t.HANDLE)
        self._open = bind('OpenProcess', t.HANDLE, t.DWORD, t.BOOL, t.DWORD)
        self._close = bind('CloseHandle', t.BOOL, t.HANDLE)
        self._error = c.WinError
        self._handle = create(None, None)
        if not self._handle:
            raise self._error()
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not set_info(self._handle, 9, c.byref(limits), c.sizeof(limits)):  # JobObjectExtendedLimitInformation
            error = self._error()
            self.close()
            raise error

    def assign(self, pid):
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE; no inheritable handles.
        process = self._open(0x0100 | 0x0001, False, pid)
        if not process:
            raise self._error()
        try:
            if not self._assign(self._handle, process):
                raise self._error()
        finally:
            self._close(process)

    def close(self):
        if self._handle:
            if not self._close(self._handle):
                raise self._error()
            self._handle = None
