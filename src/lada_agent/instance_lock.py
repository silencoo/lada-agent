"""Cross-platform process lock for a single active LADA monitor."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import BinaryIO


class MonitorInstanceLock:
    """Hold an OS-level lock without relying on a stale PID-file check."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = str(Path(path).resolve())
        self._handle: BinaryIO | None = None
        self._guard = threading.Lock()

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        with self._guard:
            if self._handle is not None:
                return True

            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            handle = open(self.path, "a+b")  # noqa: SIM115 - kept open for lock lifetime
            try:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                self._lock(handle)
            except OSError:
                handle.close()
                return False

            owner = f"pid={os.getpid()}\n".encode()
            handle.seek(0)
            handle.write(owner)
            handle.truncate()
            handle.flush()
            self._handle = handle
            return True

    def release(self) -> None:
        with self._guard:
            handle = self._handle
            if handle is None:
                return
            self._handle = None
            try:
                handle.seek(0)
                self._unlock(handle)
            finally:
                handle.close()

    @staticmethod
    def _lock(handle: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(handle: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def __enter__(self) -> MonitorInstanceLock:
        if not self.acquire():
            raise RuntimeError(f"Another LADA monitor is already using {self.path}")
        return self

    def __exit__(self, *_args) -> None:
        self.release()
