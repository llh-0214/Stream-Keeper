from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess


class SleepPreventer:
    """Keep the computer awake without forcing the display to stay on."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._windows_active = False

    def start(self) -> None:
        system = platform.system()
        if system == "Darwin" and self._process is None:
            executable = shutil.which("caffeinate")
            if executable:
                # -i prevents idle system sleep. Deliberately omit -d so the
                # display may still turn off according to the user's settings.
                self._process = subprocess.Popen(
                    [executable, "-i", "-w", str(os.getpid())],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        elif system == "Windows" and not self._windows_active:
            es_continuous = 0x80000000
            es_system_required = 0x00000001
            result = ctypes.windll.kernel32.SetThreadExecutionState(
                es_continuous | es_system_required
            )
            self._windows_active = bool(result)

    def stop(self) -> None:
        if self._process is not None:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            self._process = None
        if platform.system() == "Windows" and self._windows_active:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            self._windows_active = False

    @property
    def active(self) -> bool:
        if platform.system() == "Darwin":
            return bool(self._process and self._process.poll() is None)
        return self._windows_active
