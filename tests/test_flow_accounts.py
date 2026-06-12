"""Тесты мульти-аккаунтного слоя: parse_flow_accounts + AccountPool (flow_core)
и проводка пула в flow_bot.py (текстовые ассерты, без aiogram/сети)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import flow_core
from flow_core import AccountPool, FlowAccount, parse_flow_accounts

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ParseFlowAccountsTests(unittest.TestCase):
    def test_empty_env_gives_single_default_account(self) -> None:
        accs = parse_flow_accounts("", default_id="main", default_dir="./google_profile")
        self.assertEqual(accs, [FlowAccount(id="main", profile_dir="./google_profile")])
        self.assertEqual(parse_flow_accounts(None), [
            FlowAccount(id="default", profile_dir="./google_profile")
        ])

    def test_parses_id_eq_path_entries(self) -> None:
        accs = parse_flow_accounts("main=./google_profile;acc2=./profile2")
        self.assertEqual([a.id for a in accs], ["main", "acc2"])
        self.assertEqual(accs[1].profile_dir, "./profile2")

    def test_windows_paths_with_colon_survive(self) -> None:
        # Разделитель id/путь — '=' именно потому, что в Windows-путях есть ':'.
        accs = parse_flow_accounts(r"main=C:\profiles\one;acc2=D:\profiles\two")
        self.assertEqual(accs[0].profile_dir, r"C:\profiles\one")
        self.assertEqual(accs[1].profile_dir, r"D:\profiles\two")

    def test_bare_path_gets_generated_id_and_dups_keep_first(self) -> None:
        accs = parse_flow_accounts("./p1, main=./p2, main=./p3")
        self.assertEqual([a.id for a in accs], ["acc1", "main"])
        self.assertEqual(accs[1].profile_dir, "./p2")  # дубль id — первая запись


class AccountPoolTests(unittest.TestCase):
    def _pool(self, n: int = 2, **kw) -> AccountPool:
        accs = [FlowAccount(id=f"a{i}", profile_dir=f"./p{i}") for i in range(1, n + 1)]
        self.clock_now = 1000.0
        kw.setdefault("clock", lambda: self.clock_now)
        return AccountPool(accs, None, **kw)

    def test_sticky_assignment_and_least_loaded_spread(self) -> None:
        pool = self._pool(2)
        a_first = pool.pick_for(111)
        self.assertEqual(pool.pick_for(111), a_first)  # sticky
        a_second = pool.pick_for(222)                  # наименее загруженный
        self.assertNotEqual(a_first, a_second)
        self.assertEqual(pool.pick_for(222), a_second)

    def test_failure_cooldown_and_failover(self) -> None:
        pool = self._pool(2, max_failures=3, cooldown_sec=600)
        acc = pool.pick_for(111)
        self.assertFalse(pool.mark_failure(acc))
        self.assertFalse(pool.mark_failure(acc))
        self.assertTrue(pool.mark_failure(acc))   # 3-й сбой → кулдаун
        self.assertFalse(pool.is_available(acc))
        moved = pool.pick_for(111)                # failover на живой аккаунт
        self.assertNotEqual(moved, acc)
        # Кулдаун истёк → аккаунт снова доступен (но юзер уже переехал — sticky).
        self.clock_now += 601
        self.assertTrue(pool.is_available(acc))
        self.assertEqual(pool.pick_for(111), moved)

    def test_success_resets_failure_count(self) -> None:
        pool = self._pool(2, max_failures=2)
        acc = pool.pick_for(1)
        pool.mark_failure(acc)
        pool.mark_success(acc)
        self.assertFalse(pool.mark_failure(acc))  # счётчик сброшен — кулдауна нет
        self.assertTrue(pool.is_available(acc))

    def test_single_account_never_blocked_by_cooldown(self) -> None:
        # Падения единственного аккаунта почти наверняка системные: лучше
        # попытаться, чем молча отказывать всем (см. docstring AccountPool).
        pool = self._pool(1, max_failures=1)
        acc = pool.pick_for(5)
        self.assertTrue(pool.mark_failure(acc))
        self.assertEqual(pool.pick_for(5), acc)

    def test_all_unavailable_returns_none(self) -> None:
        pool = self._pool(2, max_failures=1)
        a1, a2 = pool.account_ids()
        pool.mark_failure(a1)
        pool.mark_failure(a2)
        self.assertIsNone(pool.pick_for(42))

    def test_manual_disable_enable(self) -> None:
        pool = self._pool(2)
        a1, a2 = pool.account_ids()
        self.assertTrue(pool.set_disabled(a1, True))
        self.assertFalse(pool.is_available(a1))
        self.assertEqual(pool.pick_for(7), a2)
        self.assertTrue(pool.set_disabled(a1, False))
        self.assertTrue(pool.is_available(a1))
        self.assertFalse(pool.set_disabled("nope", True))

    def test_video_allowed_filters_video_picker(self) -> None:
        pool = self._pool(3)
        a1, a2, a3 = pool.account_ids()
        self.assertTrue(pool.set_video_allowed(a1, False))
        self.assertTrue(pool.set_video_allowed(a2, False))
        self.assertFalse(pool.is_video_capable(a1))
        self.assertFalse(pool.is_video_capable(a2))
        self.assertEqual(pool.pick_for_video(7), a3)
        self.assertFalse(pool.set_video_allowed("missing", False))

    def test_image_picker_prefers_image_only_accounts(self) -> None:
        pool = self._pool(3)
        a1, a2, a3 = pool.account_ids()
        pool.set_video_allowed(a2, False)
        self.assertEqual(pool.pick_for_image(10, prefer_image_only=True), a2)
        self.assertEqual(pool.assigned_to(10), a2)
        self.assertEqual(pool.pick_for_video(10), a1)
        self.assertIn(
            pool.pick_for_image(10, prefer_image_only=True, exclude={a2}),
            {a1, a3},
        )

        pool.set_disabled(a2, True)
        self.assertIn(pool.pick_for_image(11, prefer_image_only=True), {a1, a3})

    def test_video_allowed_persists_false_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accounts_state.json"
            accs = [FlowAccount("a1", "./p1"), FlowAccount("a2", "./p2")]
            pool = AccountPool(accs, path, clock=lambda: 0.0)
            self.assertTrue(pool.set_video_allowed("a2", False))
            reloaded = AccountPool(accs, path, clock=lambda: 0.0)
            self.assertTrue(reloaded.is_video_capable("a1"))
            self.assertFalse(reloaded.is_video_capable("a2"))

    def test_assignments_persist_and_drop_unknown_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accounts_state.json"
            accs = [FlowAccount("a1", "./p1"), FlowAccount("a2", "./p2")]
            pool = AccountPool(accs, path, clock=lambda: 0.0)
            chosen = pool.pick_for(111)
            reloaded = AccountPool(accs, path, clock=lambda: 0.0)
            self.assertEqual(reloaded.assigned_to(111), chosen)
            # Аккаунт выбыл из конфига → его привязки отбрасываются при загрузке.
            only_other = [a for a in accs if a.id != chosen]
            shrunk = AccountPool(only_other, path, clock=lambda: 0.0)
            self.assertIsNone(shrunk.assigned_to(111))

    def test_status_snapshot_fields(self) -> None:
        pool = self._pool(2, max_failures=1, cooldown_sec=300)
        a1, _ = pool.account_ids()
        pool.pick_for(1)
        pool.mark_failure(a1)
        snap = {s["id"]: s for s in pool.status()}
        self.assertEqual(set(snap), set(pool.account_ids()))
        for s in snap.values():
            self.assertIn("disabled", s)
            self.assertIn("cooldown_left", s)
            self.assertIn("users", s)
        self.assertGreater(snap[a1]["cooldown_left"], 0)


class BotPoolWiringTests(unittest.TestCase):
    """Текстовые ассерты на проводку пула в flow_bot.py (без импорта aiogram)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (PROJECT_ROOT / "flow_bot.py").read_text(encoding="utf-8")

    def test_pool_globals_built_from_env(self) -> None:
        self.assertIn('FLOW_ACCOUNTS_RAW = os.getenv("FLOW_ACCOUNTS", "")', self.source)
        self.assertIn("FLOW_ACCOUNTS = parse_flow_accounts(", self.source)
        self.assertIn("account_pool = AccountPool(FLOW_ACCOUNTS, FLOW_ACCOUNTS_STATE_FILE)", self.source)
        self.assertIn("keepers: dict[str, SessionKeeper]", self.source)
        self.assertIn("clients: dict[str, FlowHttpClient]", self.source)
        # Алиасы первого аккаунта сохранены для одиночных/диагностических путей.
        self.assertIn("keeper = keepers[DEFAULT_ACCOUNT_ID]", self.source)
        self.assertIn("client = clients[DEFAULT_ACCOUNT_ID]", self.source)

    def test_keeper_takes_per_account_profile(self) -> None:
        self.assertIn("def __init__(self, account_id: str = \"\", profile_dir: str | None = None):", self.source)
        self.assertIn("user_data_dir=self._profile_dir or USER_DATA_DIR", self.source)

    def test_projects_are_per_account_with_legacy_migration(self) -> None:
        self.assertIn("def _project_key(account_id: str, user_id: int) -> str:", self.source)
        start = self.source.index("async def ensure_user_project")
        block = self.source[start:start + 2200]
        self.assertIn("_project_key(acc_id, user_id)", block)
        self.assertIn("legacy = project_store.get(user_id)", block)
        self.assertIn("_keeper_for_acc(acc_id).create_new_project()", block)

    def test_generation_paths_route_and_mark_health(self) -> None:
        # Image: отказ ДО credit_gate (без цикла «списали-вернули») при пустом пуле.
        gstart = self.source.index("async def _generate_and_send")
        gblock = self.source[gstart:self.source.index("def _ms_since", gstart)]
        self.assertLess(
            gblock.index('flow_copy.msg("accounts_unavailable")'),
            gblock.index("credit_gate("),
        )
        # Image: роутинг по аккаунту + health-отметки.
        start = self.source.index("async def _do_generate_and_send")
        block = self.source[start:start + 2600]
        self.assertIn("acc_id = _account_for_image(user_id)", block)
        self.assertIn('flow_copy.msg("accounts_unavailable")', block)
        self.assertIn("account_pool.mark_failure(acc_id)", block)
        self.assertIn("account_pool.mark_success(acc_id)", block)
        # Video: правки/extend остаются на аккаунте исходного ролика.
        vstart = self.source.index("async def _do_video_generate_and_send")
        vblock = self.source[vstart:vstart + 8000]
        self.assertIn("source_video.account_id if source_video and source_video.account_id", vblock)
        self.assertIn('flow_copy.msg("accounts_unavailable")', vblock)
        self.assertIn("_client_for_acc(acc_id).generate_video(", vblock)

    def test_refs_carry_account_id(self) -> None:
        self.assertIn("account_id=acc_id", self.source)
        self.assertIn("account_id=ref.account_id", self.source)
        self.assertIn("account_id: str | None = None", (PROJECT_ROOT / "flow_core.py").read_text(encoding="utf-8"))

    def test_main_starts_all_keepers_and_disables_failed(self) -> None:
        start = self.source.index("async def main")
        block = self.source[start:start + 2400]
        self.assertIn("for acc_id, kp in keepers.items():", block)
        self.assertIn("account_pool.set_disabled(acc_id, True)", block)
        self.assertIn("if not started_any:", block)

    def test_admin_pool_commands(self) -> None:
        self.assertIn('Command("acc_off")', self.source)
        self.assertIn('Command("acc_on")', self.source)
        self.assertIn("account_pool.status()", self.source)

    def test_flow_jobs_log_real_account(self) -> None:
        # Метрики flow_jobs пишут фактический аккаунт джобы, не статичный ярлык.
        self.assertIn("account_id=acc_id", self.source)
        self.assertIn("account_pool.assigned_to(user_id) or FLOW_ACCOUNT_ID", self.source)

    def test_image_upload_prefers_image_only_and_video_upload_uses_video_account(self) -> None:
        self.assertIn("def _account_for_image", self.source)
        photo_start = self.source.index("async def handle_photo")
        photo_block = self.source[photo_start:photo_start + 5200]
        self.assertIn("_account_for_image(user_id, prefer_image_only=True)", photo_block)
        self.assertIn("ensure_user_project(user_id, account_id=acc_id)", photo_block)
        self.assertIn("_keeper_for_acc(acc_id).upload_image", photo_block)

        helper_start = self.source.index("async def _upload_photo_source_from_message")
        helper_block = self.source[helper_start:helper_start + 1800]
        self.assertIn("acc_id = _account_for_video(user_id)", helper_block)
        self.assertIn('source.setdefault("_account_id", acc_id)', helper_block)

        video_start = self.source.index("async def _do_video_generate_and_send")
        video_block = self.source[video_start:video_start + 4200]
        self.assertIn("ref_acc_id = _video_reference_account_id(st, vmode)", video_block)
        self.assertIn("video_project_id = (", video_block)


if __name__ == "__main__":
    unittest.main()
