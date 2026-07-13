"""Offline backup/verify/restore tests using temporary state only."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tools.runtime_backup import (
    create_backup,
    load_state_files,
    main,
    restore_backup,
    verify_backup,
)


class RuntimeBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "runtime"
        self.root.mkdir()
        self.db = self.root / "metrics.db"
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TABLE sample(value TEXT)")
            conn.execute("INSERT INTO sample VALUES('preserved')")
            conn.commit()
        (self.root / "user_projects.json").write_text(
            json.dumps({"user": "project"}), encoding="utf-8"
        )
        self.backup = self.base / "backup"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_backup_is_consistent_and_verifiable(self) -> None:
        manifest = create_backup(
            self.root,
            self.backup,
            includes=("metrics.db", "user_projects.json", "missing.json"),
        )

        self.assertEqual(len(manifest["files"]), 2)
        verified = verify_backup(self.backup)
        self.assertEqual(verified["files"], manifest["files"])
        with closing(sqlite3.connect(self.backup / "metrics.db")) as conn:
            self.assertEqual(conn.execute("SELECT value FROM sample").fetchone()[0], "preserved")

    def test_verify_rejects_tampering(self) -> None:
        create_backup(self.root, self.backup, includes=("user_projects.json",))
        (self.backup / "user_projects.json").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            verify_backup(self.backup)

    def test_verify_rejects_duplicate_manifest_paths(self) -> None:
        create_backup(self.root, self.backup, includes=("user_projects.json",))
        manifest_path = self.backup / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"].append(dict(manifest["files"][0]))
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            verify_backup(self.backup)

    def test_restore_requires_both_explicit_guards(self) -> None:
        create_backup(self.root, self.backup, includes=("metrics.db",))
        with self.assertRaises(PermissionError):
            restore_backup(self.backup, self.base / "restore")
        with self.assertRaises(PermissionError):
            restore_backup(
                self.backup,
                self.base / "restore",
                approve_restore=True,
            )

    def test_guarded_restore_recreates_verified_state(self) -> None:
        create_backup(
            self.root,
            self.backup,
            includes=("metrics.db", "user_projects.json"),
        )
        restored_root = self.base / "restore"

        count = restore_backup(
            self.backup,
            restored_root,
            approve_restore=True,
            service_stopped=True,
        )

        self.assertEqual(count, 2)
        self.assertEqual(
            json.loads((restored_root / "user_projects.json").read_text(encoding="utf-8")),
            {"user": "project"},
        )
        with closing(sqlite3.connect(restored_root / "metrics.db")) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_output_and_outside_sources_are_refused(self) -> None:
        self.backup.mkdir()
        with self.assertRaises(FileExistsError):
            create_backup(self.root, self.backup)
        self.backup.rmdir()
        outside = self.base / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inside root"):
            create_backup(self.root, self.backup, includes=(outside,))

    def test_env_file_selects_custom_runtime_paths(self) -> None:
        custom = self.root / "state" / "custom-metrics.db"
        custom.parent.mkdir()
        custom.write_bytes(b"not opened because only path resolution is tested")
        env_file = self.root / ".env"
        env_file.write_text("METRICS_DB=state/custom-metrics.db\n", encoding="utf-8")

        paths = load_state_files(env_file)

        self.assertIn("state/custom-metrics.db", paths)
        self.assertIn("user_credits.json", paths)

    def test_restore_refuses_symlink_target(self) -> None:
        create_backup(self.root, self.backup, includes=("user_projects.json",))
        restored_root = self.base / "restore"
        restored_root.mkdir()
        target = restored_root / "user_projects.json"
        outside = self.base / "outside-target.json"
        outside.write_text("unchanged", encoding="utf-8")
        try:
            target.symlink_to(outside)
        except OSError:
            self.skipTest("symlinks are unavailable")

        with self.assertRaisesRegex(ValueError, "symlink"):
            restore_backup(
                self.backup,
                restored_root,
                approve_restore=True,
                service_stopped=True,
            )
        self.assertEqual(outside.read_text(encoding="utf-8"), "unchanged")

    def test_cli_combines_multiple_env_files(self) -> None:
        first_state = self.root / "first.json"
        second_state = self.root / "second.json"
        first_state.write_text("{}", encoding="utf-8")
        second_state.write_text("{}", encoding="utf-8")
        first_env = self.root / "consumer.env"
        second_env = self.root / "seller.env"
        first_env.write_text("USER_PROJECTS_FILE=first.json\n", encoding="utf-8")
        second_env.write_text("USER_CREDITS_FILE=second.json\n", encoding="utf-8")

        result = main(
            [
                "backup",
                "--root",
                str(self.root),
                "--output",
                str(self.backup),
                "--env-file",
                str(first_env),
                "--env-file",
                str(second_env),
            ]
        )

        self.assertEqual(result, 0)
        paths = {record["path"] for record in verify_backup(self.backup)["files"]}
        self.assertIn("first.json", paths)
        self.assertIn("second.json", paths)

    def test_restore_rolls_back_all_targets_after_replace_failure(self) -> None:
        second = self.root / "second.json"
        second.write_text('{"version": "backup"}', encoding="utf-8")
        create_backup(
            self.root,
            self.backup,
            includes=("user_projects.json", "second.json"),
        )
        (self.root / "user_projects.json").write_text(
            '{"version": "current-one"}', encoding="utf-8"
        )
        second.write_text('{"version": "current-two"}', encoding="utf-8")
        real_replace = __import__("os").replace
        failed = False

        def fail_second_replace(source, target):
            nonlocal failed
            if Path(target) == second and ".restore-" in Path(source).name and not failed:
                failed = True
                raise OSError("simulated replace failure")
            return real_replace(source, target)

        with mock.patch("tools.runtime_backup.os.replace", side_effect=fail_second_replace):
            with self.assertRaisesRegex(OSError, "simulated"):
                restore_backup(
                    self.backup,
                    self.root,
                    approve_restore=True,
                    service_stopped=True,
                )

        self.assertEqual(
            (self.root / "user_projects.json").read_text(encoding="utf-8"),
            '{"version": "current-one"}',
        )
        self.assertEqual(second.read_text(encoding="utf-8"), '{"version": "current-two"}')


if __name__ == "__main__":
    unittest.main()
