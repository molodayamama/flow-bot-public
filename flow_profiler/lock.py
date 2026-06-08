from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator


class LockError(RuntimeError):
    """Raised when another profiler run owns the lock."""


class FlowProfilerLock:
    def __init__(self, output_root: Path, *, stale_after_sec: float = 3600) -> None:
        self.output_root = output_root
        self.lock_path = output_root / ".flow_profiler.lock"
        self.stale_after_sec = stale_after_sec
        self._owned = False

    def acquire(self) -> "FlowProfilerLock":
        self.output_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "created_at": time.time(),
            "status": "active",
        }
        if self._try_create_active_lock(payload):
            self._owned = True
            return self
        self._reuse_existing_lock(payload)
        self._owned = True
        return self

    def release(self) -> None:
        if not self._owned:
            return
        payload = {
            "pid": os.getpid(),
            "released_at": time.time(),
            "status": "released",
        }
        self.lock_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        self._owned = False

    def __enter__(self) -> "FlowProfilerLock":
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()

    def _try_create_active_lock(self, payload: dict[str, object]) -> bool:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            descriptor = os.open(str(self.lock_path), flags)
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
        return True

    def _reuse_existing_lock(self, payload: dict[str, object]) -> None:
        try:
            with self.lock_path.open("r+b") as handle:
                with _exclusive_file_guard(handle):
                    if not self._can_reuse_existing_lock(handle):
                        raise LockError("another profiler run is active")
                    handle.seek(0)
                    handle.truncate()
                    handle.write(json.dumps(payload, sort_keys=True).encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
        except FileNotFoundError:
            if self._try_create_active_lock(payload):
                return
            self._reuse_existing_lock(payload)

    def _can_reuse_existing_lock(self, handle: BinaryIO) -> bool:
        if self._is_released_lock(handle):
            return True
        try:
            age = time.time() - self.lock_path.stat().st_mtime
        except FileNotFoundError:
            return True
        return age > self.stale_after_sec

    def _is_released_lock(self, handle: BinaryIO) -> bool:
        try:
            handle.seek(0)
            payload = json.loads(handle.read().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return payload.get("status") == "released"


@contextmanager
def _exclusive_file_guard(handle: BinaryIO) -> Iterator[None]:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise LockError("another profiler run is acquiring the lock") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise LockError("another profiler run is acquiring the lock") from exc
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
