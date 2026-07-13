"""Consistent local runtime-state backup, verification, and guarded restore."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from dotenv import dotenv_values


DEFAULT_STATE_FILES = (
    "metrics.db",
    "max_webhook_inbox.db",
    "user_credits.json",
    "user_projects.json",
    "payments.json",
    "flow_accounts_state.json",
    "account_metadata.json",
    "recent_images.json",
    "edit_capture.json",
    "edit_capture_raw.json",
    "upload_capture.json",
    "upscale_capture.json",
    "config_override.json",
    "local_proxies.json",
)
MANIFEST = "manifest.json"

ENV_STATE_FILES = {
    "METRICS_DB": "metrics.db",
    "MAX_INBOX_DB": "max_webhook_inbox.db",
    "USER_CREDITS_FILE": "user_credits.json",
    "USER_PROJECTS_FILE": "user_projects.json",
    "PAYMENTS_FILE": "payments.json",
    "FLOW_ACCOUNTS_STATE_FILE": "flow_accounts_state.json",
    "ACCOUNT_METADATA_FILE": "account_metadata.json",
    "RECENT_IMAGES_FILE": "recent_images.json",
    "EDIT_CAPTURE_FILE": "edit_capture.json",
    "EDIT_CAPTURE_RAW_FILE": "edit_capture_raw.json",
    "UPLOAD_CAPTURE_FILE": "upload_capture.json",
    "UPSCALE_CAPTURE_FILE": "upscale_capture.json",
    "CONFIG_OVERRIDE_FILE": "config_override.json",
    "LOCAL_PROXIES_FILE": "local_proxies.json",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_source(root: Path, raw: str | Path) -> tuple[Path, Path]:
    source = Path(raw)
    source = source if source.is_absolute() else root / source
    if source.is_symlink():
        raise ValueError("backup source must not be a symlink")
    resolved = source.resolve()
    try:
        relative = resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("backup source must stay inside root") from exc
    return resolved, relative


def _copy_sqlite(source: Path, target: Path) -> None:
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=10.0)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)


def _copy_state(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".db":
        _copy_sqlite(source, target)
        kind = "sqlite"
    else:
        shutil.copy2(source, target)
        kind = "file"
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return kind


def state_files_from_environment(env: Mapping[str, str]) -> tuple[str, ...]:
    """Resolve all known runtime-state paths without exposing their contents."""
    paths = []
    for name, default in ENV_STATE_FILES.items():
        value = str(env.get(name, "") or "").strip()
        paths.append(value or default)
    return tuple(dict.fromkeys(paths))


def load_state_files(env_file: str | Path) -> tuple[str, ...]:
    values = {key: str(value or "") for key, value in dotenv_values(env_file).items()}
    return state_files_from_environment(values)


def create_backup(
    root: str | Path,
    output: str | Path,
    *,
    includes: Iterable[str | Path] = DEFAULT_STATE_FILES,
) -> dict:
    root_path = Path(root).resolve()
    output_path = Path(output).resolve()
    if output_path.exists():
        raise FileExistsError("backup output already exists")
    output_path.mkdir(parents=True, mode=0o700)
    records = []
    try:
        for raw in includes:
            source, relative = _relative_source(root_path, raw)
            if not source.exists():
                continue
            if not source.is_file():
                raise ValueError("backup source must be a regular file")
            target = output_path / relative
            kind = _copy_state(source, target)
            records.append(
                {
                    "path": relative.as_posix(),
                    "kind": kind,
                    "size": target.stat().st_size,
                    "sha256": _sha256(target),
                }
            )
        manifest = {
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": records,
        }
        manifest_path = output_path / MANIFEST
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            manifest_path.chmod(0o600)
        except OSError:
            pass
        return manifest
    except BaseException:
        shutil.rmtree(output_path, ignore_errors=True)
        raise


def verify_backup(backup: str | Path) -> dict:
    backup_path = Path(backup).resolve()
    manifest_path = backup_path / MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError("unsupported backup manifest")
    seen_paths: set[str] = set()
    for record in manifest["files"]:
        relative = Path(str(record.get("path", "")))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe backup manifest path")
        normalized = relative.as_posix()
        if normalized in seen_paths:
            raise ValueError("duplicate backup manifest path")
        seen_paths.add(normalized)
        target = (backup_path / relative).resolve()
        try:
            target.relative_to(backup_path)
        except ValueError as exc:
            raise ValueError("unsafe backup manifest path") from exc
        if not target.is_file():
            raise ValueError("backup file is missing")
        if target.stat().st_size != int(record.get("size", -1)):
            raise ValueError("backup file size mismatch")
        if _sha256(target) != str(record.get("sha256", "")):
            raise ValueError("backup file checksum mismatch")
        kind = record.get("kind")
        if kind not in {"file", "sqlite"}:
            raise ValueError("unsupported backup file kind")
        if kind == "sqlite":
            with closing(
                sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
            ) as conn:
                result = conn.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise ValueError("SQLite integrity check failed")
    return manifest


def restore_backup(
    backup: str | Path,
    root: str | Path,
    *,
    approve_restore: bool = False,
    service_stopped: bool = False,
) -> int:
    if not approve_restore or not service_stopped:
        raise PermissionError(
            "restore requires approve_restore=True and service_stopped=True"
        )
    manifest = verify_backup(backup)
    backup_path = Path(backup).resolve()
    root_path = Path(root).resolve()
    staged: list[tuple[Path, Path]] = []
    committed: list[tuple[Path, Path | None]] = []
    preserve_previous: set[Path] = set()
    try:
        # Copy everything first. No runtime file is changed until all sources
        # have been staged successfully in their destination filesystems.
        for record in manifest["files"]:
            relative = Path(record["path"])
            source = backup_path / relative
            target = root_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.parent.resolve().relative_to(root_path)
            except ValueError as exc:
                raise ValueError("restore target must stay inside root") from exc
            if target.is_symlink():
                raise ValueError("restore target must not be a symlink")
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.restore-", dir=target.parent
            )
            os.close(fd)
            temp_path = Path(temp_name)
            shutil.copy2(source, temp_path)
            staged.append((temp_path, target))

        # Keep the old files beside their targets until every atomic replace
        # succeeds, so an I/O failure can roll the complete set back.
        for temp_path, target in staged:
            previous: Path | None = None
            if target.exists():
                fd, previous_name = tempfile.mkstemp(
                    prefix=f".{target.name}.previous-", dir=target.parent
                )
                os.close(fd)
                previous = Path(previous_name)
                try:
                    os.replace(target, previous)
                except BaseException:
                    previous.unlink(missing_ok=True)
                    raise
            committed.append((target, previous))
            os.replace(temp_path, target)
            try:
                target.chmod(0o600)
            except OSError:
                pass
        return len(committed)
    except BaseException as original:
        rollback_failures = 0
        for target, previous in reversed(committed):
            try:
                if previous is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(previous, target)
            except BaseException:
                rollback_failures += 1
                if previous is not None:
                    preserve_previous.add(previous)
        if rollback_failures:
            raise RuntimeError(
                "restore failed and rollback was incomplete; previous files were preserved"
            ) from original
        raise
    finally:
        for temp_path, _target in staged:
            temp_path.unlink(missing_ok=True)
        for _target, previous in committed:
            if previous is not None and previous not in preserve_previous:
                previous.unlink(missing_ok=True)


def _default_output() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"runtime-backup-{stamp}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    backup_cmd = sub.add_parser("backup")
    backup_cmd.add_argument("--root", default=".")
    backup_cmd.add_argument("--output", default=_default_output())
    backup_cmd.add_argument("--include", action="append", dest="includes")
    backup_cmd.add_argument(
        "--env-file",
        action="append",
        dest="env_files",
        help="resolve known runtime-state paths from this dotenv file",
    )
    verify_cmd = sub.add_parser("verify")
    verify_cmd.add_argument("backup")
    restore_cmd = sub.add_parser("restore")
    restore_cmd.add_argument("backup")
    restore_cmd.add_argument("--root", default=".")
    restore_cmd.add_argument("--approve-restore", action="store_true")
    restore_cmd.add_argument("--service-stopped", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "backup":
        includes = args.includes
        if includes is None and args.env_files:
            includes = tuple(
                dict.fromkeys(
                    path
                    for env_file in args.env_files
                    for path in load_state_files(env_file)
                )
            )
        manifest = create_backup(
            args.root,
            args.output,
            includes=includes or DEFAULT_STATE_FILES,
        )
        print(f"backup created: {len(manifest['files'])} file(s)")
    elif args.command == "verify":
        manifest = verify_backup(args.backup)
        print(f"backup verified: {len(manifest['files'])} file(s)")
    else:
        restored = restore_backup(
            args.backup,
            args.root,
            approve_restore=args.approve_restore,
            service_stopped=args.service_stopped,
        )
        print(f"backup restored: {restored} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
