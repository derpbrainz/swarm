"""Versioned local saves, atomic replacement, and a process-scoped writer lease."""

from contextlib import contextmanager
import fcntl
import json
import logging
import os
from pathlib import Path
import tempfile


class SaveError(RuntimeError):
    pass


def runtime_directory() -> Path:
    return Path(os.environ.get("SWARM_SAVE_DIR", ".swarm-saves")).resolve()


def read_json(path: Path):
    def reject_constant(value):
        raise ValueError(f"nonfinite value {value}")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (OSError, ValueError) as exc:
        raise SaveError(f"Cannot read save {path}: {exc}; original retained") from exc


def read_snapshot(path: Path, key: str) -> dict:
    data = read_json(path)
    if not isinstance(data, dict):
        raise SaveError(f"{path}: expected object, original retained")
    if "schema_version" in data:
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise SaveError(f"{path}: unsupported schema version; original retained")
        data = data.get(key)
        if not isinstance(data, dict):
            raise SaveError(f"{path}: missing {key} object; original retained")
    return data


def _replace_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_snapshot(path: Path, key: str, data: dict, *, metadata: dict | None = None) -> None:
    payload = None
    try:
        payload = json.dumps({"schema_version": 1, key: data, **(metadata or {})}, indent=2, allow_nan=False).encode("utf-8")
        if path.exists():
            read_snapshot(path, key)
            _replace_bytes(path.with_suffix(path.suffix + ".bak"), path.read_bytes())
        _replace_bytes(path, payload)
    except (OSError, ValueError) as exc:
        # A replacement followed by a directory-fsync error already committed.
        # Reconcile publication with the file, rather than refunding a committed transfer.
        try:
            if path.read_bytes() == payload:
                logging.getLogger(__name__).warning("Save %s replaced successfully but durability confirmation failed: %s", path, exc)
                return
        except OSError:
            pass
        raise SaveError(f"Cannot save {path}: {exc}; inspect original and .bak before retry") from exc


def recover_backup(path: Path, key: str) -> None:
    """Explicit recovery retains the damaged file; startup never recovers silently."""
    backup = path.with_suffix(path.suffix + ".bak")
    read_snapshot(backup, key)
    if path.exists():
        _replace_bytes(path.with_suffix(path.suffix + ".damaged"), path.read_bytes())
    _replace_bytes(path, backup.read_bytes())


@contextmanager
def writer_lease(paths):
    handles = []
    try:
        for path in sorted({Path(p).resolve() for p in paths}):
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.with_suffix(path.suffix + ".lock").open("a+b")
            handles.append(handle)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SaveError(f"Another server owns {path}; multiple writers are unsupported") from exc
        yield
    finally:
        for handle in reversed(handles):
            handle.close()
