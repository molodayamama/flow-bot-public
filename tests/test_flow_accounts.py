"""Тесты мульти-аккаунтного слоя: parse_flow_accounts + AccountPool (flow_core)
и проводка пула в flow_bot.py (текстовые ассерты, без aiogram/сети)."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

import flow_core
from accounts import AccountPool as AccountsPool
from accounts import FlowAccount as AccountsFlowAccount
from accounts import parse_flow_accounts as accounts_parse_flow_accounts
from flow_core import AccountPool, FlowAccount, parse_flow_accounts

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_PR2A_PROVIDER_SOURCE = (
    (PROJECT_ROOT / "flow_provider" / "client.py").read_text(encoding="utf-8")
    + "\n"
    + (PROJECT_ROOT / "flow_provider" / "runtime_config.py").read_text(encoding="utf-8")
)


class ParseFlowAccountsTests(unittest.TestCase):
    def test_accounts_package_is_canonical_export(self) -> None:
        self.assertIs(AccountsPool, AccountPool)
        self.assertIs(AccountsFlowAccount, FlowAccount)
        self.assertIs(accounts_parse_flow_accounts, parse_flow_accounts)

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


    def test_account_proxy_option_sets_browser_and_api_proxy(self) -> None:
        accs = parse_flow_accounts(
            "main=./google_profile;"
            "acc2=./profile2|proxy=http://127.0.0.1:8118"
        )
        self.assertIsNone(accs[0].browser_proxy_url)
        self.assertIsNone(accs[0].api_proxy_url)
        self.assertEqual(accs[1].profile_dir, "./profile2")
        self.assertEqual(accs[1].browser_proxy_url, "http://127.0.0.1:8118")
        self.assertEqual(accs[1].api_proxy_url, "http://127.0.0.1:8118")

    def test_account_proxy_options_can_split_browser_and_api(self) -> None:
        accs = parse_flow_accounts(
            "acc2=./profile2|browser_proxy=direct|api_proxy=http://127.0.0.1:8120"
        )
        self.assertEqual(accs[0].browser_proxy_url, "direct")
        self.assertEqual(accs[0].api_proxy_url, "http://127.0.0.1:8120")

    def test_account_proxy_option_decodes_separator_safe_value(self) -> None:
        proxy = "http://user:p%40" + "ss%7Cword@127.0.0.1:8118"
        accs = parse_flow_accounts(
            "acc2=./profile2|proxy=" + proxy
        )
        expected = "http://user" + ":p@" + "ss|word@127.0.0.1:8118"
        self.assertEqual(accs[0].browser_proxy_url, expected)
        self.assertEqual(accs[0].api_proxy_url, expected)


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

    def test_add_account_hot_adds_runtime_state(self) -> None:
        pool = self._pool(1)
        added = pool.add_account(FlowAccount(id="a2", profile_dir="./p2"), runtime_status="warming")

        self.assertTrue(added)
        self.assertFalse(pool.add_account(FlowAccount(id="a2", profile_dir="./p2")))
        by_id = {row["id"]: row for row in pool.status()}
        self.assertIn("a2", by_id)
        self.assertFalse(by_id["a2"]["runtime_ready"])
        self.assertEqual(by_id["a2"]["runtime_status"], "warming")
        self.assertEqual(by_id["a2"]["active_image_jobs"], 0)

    def test_remove_account_drops_runtime_state(self) -> None:
        pool = self._pool(2)

        self.assertTrue(pool.remove_account("a2"))
        self.assertNotIn("a2", pool.account_ids())
        self.assertFalse(pool.remove_account("a1"))  # keep at least one account

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

    def test_mark_cooldown_bypasses_failure_threshold(self) -> None:
        pool = self._pool(2, max_failures=3, cooldown_sec=600)
        acc = pool.pick_for(1)
        self.assertTrue(pool.mark_cooldown(acc))
        self.assertFalse(pool.is_available(acc))
        self.assertNotEqual(pool.pick_for(1), acc)
        self.clock_now += 601
        self.assertTrue(pool.is_available(acc))

    def test_cooldown_blocks_both_image_and_video_routing(self) -> None:
        # Один cooldown (напр. после провайдерского 429) должен убрать аккаунт
        # из маршрутизации И картинок, И видео — пул держит общий cooldown.
        pool = self._pool(2, max_failures=3, cooldown_sec=600)
        acc = pool.pick_for(7)
        self.assertTrue(pool.is_video_capable(acc))   # видео доступно до кулдауна
        self.assertTrue(pool.mark_cooldown(acc))
        self.assertFalse(pool.is_available(acc))       # картинки: недоступен
        self.assertFalse(pool.is_video_capable(acc))   # видео: тоже недоступен
        self.assertNotEqual(pool.pick_for_image(7), acc)
        self.assertNotEqual(pool.pick_for_video(7), acc)
        # По истечении кулдауна аккаунт снова в строю для обоих типов.
        self.clock_now += 601
        self.assertTrue(pool.is_available(acc))
        self.assertTrue(pool.is_video_capable(acc))

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

    def test_needs_relogin_pulls_from_rotation_and_recovers(self) -> None:
        pool = self._pool(2)
        a1, a2 = pool.account_ids()

        # 401 от credits / нет project_id → флаг релогина выводит из ротации.
        self.assertTrue(pool.mark_needs_relogin(a1, True))
        self.assertFalse(pool.is_available(a1))
        self.assertEqual(pool.pick_for(7), a2)  # маршрутизация уходит на здоровый
        by_id = {row["id"]: row for row in pool.status()}
        self.assertTrue(by_id[a1]["needs_relogin"])

        # Успешная джоба = логин жив → флаг снимается автоматически.
        pool.mark_success(a1)
        self.assertTrue(pool.is_available(a1))
        self.assertFalse({r["id"]: r for r in pool.status()}[a1]["needs_relogin"])

        # Ручное включение тоже снимает флаг (релогин/онбординг починили).
        pool.mark_needs_relogin(a1, True)
        self.assertFalse(pool.is_available(a1))
        pool.set_disabled(a1, False)
        self.assertTrue(pool.is_available(a1))

        self.assertFalse(pool.mark_needs_relogin("nope", True))

    def test_video_allowed_filters_video_picker(self) -> None:
        pool = self._pool(3)
        a1, a2, a3 = pool.account_ids()
        self.assertTrue(pool.set_video_allowed(a1, False))
        self.assertTrue(pool.set_video_allowed(a2, False))
        self.assertFalse(pool.is_video_capable(a1))
        self.assertFalse(pool.is_video_capable(a2))
        self.assertEqual(pool.pick_for_video(7), a3)
        self.assertFalse(pool.set_video_allowed("missing", False))

    def test_video_picker_prefers_health_score_over_sticky(self) -> None:
        pool = self._pool(2)
        a1, a2 = pool.account_ids()
        self.assertEqual(pool.pick_for(7), a1)  # sticky for image work

        picked = pool.pick_for_video(
            7,
            model_family="veo",
            health_scores={
                a1: {"score": 10, "model_family": "veo"},
                a2: {"score": 90, "model_family": "veo"},
            },
        )

        self.assertEqual(picked, a2)
        self.assertEqual(pool.assigned_to(7), a1)

    def test_video_picker_excludes_proxy_failed_accounts(self) -> None:
        pool = self._pool(2)
        a1, a2 = pool.account_ids()

        picked = pool.pick_for_video(
            42,
            model_family="veo",
            health_scores={
                a1: {"score": 100, "model_family": "veo", "proxy_failed": True},
                a2: {"score": 10, "model_family": "veo"},
            },
        )

        self.assertEqual(picked, a2)

    def test_runtime_ready_blocks_warming_accounts(self) -> None:
        pool = self._pool(2)
        a1, a2 = pool.account_ids()
        self.assertTrue(pool.set_runtime_ready(a1, False, "warming"))
        self.assertFalse(pool.is_available(a1))
        self.assertFalse(pool.is_video_capable(a1))
        self.assertEqual(pool.pick_for_video(42), a2)
        status = {s["id"]: s for s in pool.status()}
        self.assertEqual(status[a1]["runtime_status"], "warming")
        self.assertFalse(status[a1]["runtime_ready"])

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
        cls.animate_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "animate.py"
        ).read_text(encoding="utf-8")
        # /acc_off /acc_on /acc_vid_off /acc_vid_on moved to their own router
        # (Phase 6).
        cls.admin_accounts_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "admin_accounts.py"
        ).read_text(encoding="utf-8")
        cls.admin_status_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "admin_status.py"
        ).read_text(encoding="utf-8")
        # Video generate-and-send moved to channels.telegram.video_flow (Phase 11);
        # deps injected, so account/pool refs carry a ``d.`` prefix there.
        cls.video_flow_source = (
            PROJECT_ROOT / "channels" / "telegram" / "video_flow.py"
        ).read_text(encoding="utf-8")

    def test_pool_globals_built_from_env(self) -> None:
        self.assertIn('FLOW_ACCOUNTS_RAW = os.getenv("FLOW_ACCOUNTS", "")', self.source)
        self.assertIn("FLOW_ACCOUNTS = parse_flow_accounts(", self.source)
        self.assertIn("account_pool = AccountPool(", self.source)
        self.assertIn("FLOW_ACCOUNTS, FLOW_ACCOUNTS_STATE_FILE", self.source)
        self.assertIn("keepers: dict[str, SessionKeeper]", self.source)
        self.assertIn("clients: dict[str, FlowHttpClient]", self.source)
        # Алиасы первого аккаунта сохранены для одиночных/диагностических путей.
        self.assertIn("keeper = keepers[DEFAULT_ACCOUNT_ID]", self.source)
        self.assertIn("client = clients[DEFAULT_ACCOUNT_ID]", self.source)

    def test_keeper_takes_per_account_profile(self) -> None:
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        self.assertIn("browser_proxy_url: str | None = None", self.source)
        self.assertIn("api_proxy_url: str | None = None", self.source)
        self.assertIn("user_data_dir=self._profile_dir or USER_DATA_DIR", self.source)
        self.assertIn("browser_proxy_url=acc.browser_proxy_url", self.source)
        self.assertIn("api_proxy_url=acc.api_proxy_url", self.source)

    def test_projects_are_per_account_with_legacy_migration(self) -> None:
        # flow_bot keeps thin delegates; the sticky/legacy logic moved to
        # accounts/projects.py::ProjectManager (Phase 11 core split).
        self.assertIn("def _project_key(account_id: str, user_id: int) -> str:", self.source)
        self.assertIn("_project_mgr.ensure(user_id, account_id=account_id)", self.source)
        pm = (PROJECT_ROOT / "accounts" / "projects.py").read_text(encoding="utf-8")
        self.assertIn("def project_key(account_id: str, user_id: int) -> str:", pm)
        self.assertIn("self.project_key(acc_id, user_id)", pm)
        self.assertIn("legacy = self._store.get(user_id)", pm)
        self.assertIn("self._keeper_for_acc(acc_id).create_new_project()", pm)

    def test_generation_paths_route_and_mark_health(self) -> None:
        # Image: отказ ДО credit_gate (без цикла «списали-вернули») при пустом пуле.
        gen_flow = (PROJECT_ROOT / "channels" / "telegram" / "generation_flow.py").read_text(encoding="utf-8")
        gstart = gen_flow.index("async def generate_and_send")
        gblock = gen_flow[gstart:gen_flow.index("async def do_generate_and_send", gstart)]
        self.assertLess(
            gblock.index('flow_copy.msg("accounts_unavailable")'),
            gblock.index("credit_gate("),
        )
        # Image: роутинг по аккаунту + health-отметки.
        # do_generate_and_send moved to channels.telegram.generation_flow (Phase 11).
        block = gen_flow[gen_flow.index("async def do_generate_and_send"):]
        self.assertIn("acc_id = d.account_for_image(user_id", block)  # may have exclude= kwarg
        self.assertIn('flow_copy.msg("accounts_unavailable")', block)
        self.assertIn("d.account_pool.mark_failure(acc_id)", block)
        self.assertIn("d.account_pool.mark_success(acc_id)", block)
        # Video: правки/extend остаются на аккаунте исходного ролика.
        vblock = self.video_flow_source
        self.assertIn("if source_video and source_video.account_id:", vblock)
        self.assertIn('flow_copy.msg("accounts_unavailable")', vblock)
        self.assertIn("d.client_for_acc(acc_id).generate_video(", vblock)

    def test_refs_carry_account_id(self) -> None:
        self.assertIn("account_id=acc_id", self.source)
        # i2i/edit delivery (account_id=ref.account_id) moved to generation_flow.
        gen_flow = (
            PROJECT_ROOT / "channels" / "telegram" / "generation_flow.py"
        ).read_text(encoding="utf-8")
        self.assertIn("account_id=ref.account_id", gen_flow)
        self.assertIn("account_id: str | None = None", (PROJECT_ROOT / "flow_core.py").read_text(encoding="utf-8"))

    def test_animate_from_image_pins_reference_account(self) -> None:
        # «Оживить фото» под сгенерированной картинкой должно унести r2v на тот же
        # аккаунт/проект, где живёт медиа; иначе reference media → 404 на другом
        # аккаунте пула. Источник обогащается _account_id/_project_id из ImageRef.
        start = self.animate_router_source.index('if data.startswith("an:img:")')
        block = self.animate_router_source[start:start + 1200]
        self.assertIn("start_from_generated_image", block)
        self.assertIn("source=ref.source if isinstance(ref.source, dict) else {}", block)
        self.assertIn("account_id=ref.account_id", block)
        self.assertIn("project_id=ref.project_id", block)

    def test_main_starts_all_keepers_and_disables_failed(self) -> None:
        start = self.source.index("async def _main_impl")
        block = self.source[start:start + 7000]
        self.assertLess(block.index("await _start_robokassa_web_server()"),
                        block.index("await kp.start()"))
        self.assertLess(block.index("await ready_event.wait()"),
                        block.index("await dp.start_polling(bot)"))
        self.assertIn("MIN_READY_ACCOUNTS", block)
        self.assertIn("await kp.start()", block)
        self.assertIn("account_pool.set_runtime_ready(acc_id, False, \"warming\")", block)
        self.assertIn("account_pool.set_disabled(acc_id, True)", block)
        self.assertIn("if ready_count < min_ready:", block)

    def test_admin_pool_commands(self) -> None:
        # /acc_off and /acc_on live in the admin_accounts router (Phase 6).
        self.assertIn('Command("acc_off")', self.admin_accounts_router_source)
        self.assertIn('Command("acc_on")', self.admin_accounts_router_source)
        self.assertIn("account_pool=account_pool", self.source)
        self.assertIn("deps.account_pool.status()", self.admin_status_router_source)

    def test_flow_jobs_log_real_account(self) -> None:
        # Метрики flow_jobs пишут фактический аккаунт джобы, не статичный ярлык.
        self.assertIn("account_id=acc_id", self.source)
        self.assertIn("account_pool.assigned_to(user_id) or FLOW_ACCOUNT_ID", self.source)

    def test_image_upload_prefers_image_only_and_video_upload_uses_video_account(self) -> None:
        self.assertIn("def _account_for_image", self.source)
        photo_start = self.source.index("async def _upload_image_ref_from_photo_message")
        photo_block = self.source[photo_start:photo_start + 2200]
        self.assertIn("_account_for_image(user_id, prefer_image_only=True)", photo_block)
        self.assertIn("ensure_user_project(user_id, account_id=acc_id)", photo_block)
        self.assertIn("_keeper_for_acc(acc_id).upload_image", photo_block)

        helper_start = self.source.index("async def _upload_photo_source_from_message")
        helper_block = self.source[helper_start:helper_start + 1800]
        self.assertIn("acc_id = _account_for_video(user_id)", helper_block)
        self.assertIn('source.setdefault("_account_id", acc_id)', helper_block)

        video_block = self.video_flow_source
        # Photo-video re-places the reference on a healthy account seamlessly
        # (no user-facing "account unavailable" error).
        self.assertIn("d.ensure_reference_on_healthy_account(", video_block)
        self.assertIn("video_project_id = (", video_block)


class CapacityTests(unittest.IsolatedAsyncioTestCase):
    """Per-account semaphore capacity enforcement."""

    def _pool(self, n: int = 2, img_cap: int = 2, vid_cap: int = 1) -> AccountPool:
        accs = [FlowAccount(id=f"a{i}", profile_dir=f"./p{i}") for i in range(1, n + 1)]
        return AccountPool(
            accs, None,
            default_image_capacity=img_cap,
            default_video_capacity=vid_cap,
        )

    async def test_video_capacity_zero_is_image_only(self):
        pool = self._pool(1, img_cap=2, vid_cap=0)
        self.assertFalse(pool.is_video_capable("a1"))
        self.assertFalse(pool.is_reference_usable("a1"))
        self.assertTrue(pool.is_image_only("a1"))

    async def test_image_capacity_reported_correctly(self):
        pool = self._pool(2, img_cap=3, vid_cap=1)
        statuses = {s["id"]: s for s in pool.status()}
        self.assertEqual(statuses["a1"]["image_capacity"], 3)
        self.assertEqual(statuses["a1"]["video_capacity"], 1)
        self.assertEqual(statuses["a1"]["active_image_jobs"], 0)
        self.assertEqual(statuses["a1"]["active_video_jobs"], 0)

    async def test_per_account_image_capacity_override(self):
        accs = [
            FlowAccount(id="img", profile_dir="./p1", image_capacity=5),
            FlowAccount(id="vid", profile_dir="./p2", video_capacity=2),
        ]
        pool = AccountPool(accs, None, default_image_capacity=2, default_video_capacity=1)
        s = {s["id"]: s for s in pool.status()}
        self.assertEqual(s["img"]["image_capacity"], 5)   # per-account override
        self.assertEqual(s["vid"]["video_capacity"], 2)   # per-account override
        self.assertEqual(s["img"]["video_capacity"], 1)   # default
        self.assertEqual(s["vid"]["image_capacity"], 2)   # default

    async def test_image_slot_active_jobs_count(self):
        pool = self._pool(1, img_cap=2)
        acc = pool.account_ids()[0]
        self.assertEqual(pool.status()[0]["active_image_jobs"], 0)
        barrier = asyncio.Event()
        released = asyncio.Event()

        async def hold_slot():
            async with pool.image_slot(acc):
                barrier.set()
                await released.wait()

        task = asyncio.create_task(hold_slot())
        await barrier.wait()
        self.assertEqual(pool.status()[0]["active_image_jobs"], 1)
        released.set()
        await task
        self.assertEqual(pool.status()[0]["active_image_jobs"], 0)

    async def test_image_capacity_limits_concurrency(self):
        """50 coroutines on cap=2 account: only 2 run simultaneously."""
        pool = self._pool(1, img_cap=2)
        acc = pool.account_ids()[0]
        max_concurrent = 0
        current = 0
        lock = asyncio.Lock()
        done_event = asyncio.Event()

        async def one_job():
            nonlocal max_concurrent, current
            async with pool.image_slot(acc):
                async with lock:
                    current += 1
                    max_concurrent = max(max_concurrent, current)
                await asyncio.sleep(0)  # yield to let other coroutines try to enter
                async with lock:
                    current -= 1

        await asyncio.gather(*[one_job() for _ in range(50)])
        self.assertLessEqual(max_concurrent, 2)
        self.assertEqual(pool.status()[0]["active_image_jobs"], 0)

    async def test_video_capacity_one_at_a_time(self):
        """video cap=1 means at most 1 video job per account."""
        pool = self._pool(1, vid_cap=1)
        acc = pool.account_ids()[0]
        max_concurrent = 0
        current = 0
        lock = asyncio.Lock()

        async def one_vid():
            nonlocal max_concurrent, current
            async with pool.video_slot(acc):
                async with lock:
                    current += 1
                    max_concurrent = max(max_concurrent, current)
                await asyncio.sleep(0)
                async with lock:
                    current -= 1

        await asyncio.gather(*[one_vid() for _ in range(10)])
        self.assertLessEqual(max_concurrent, 1)
        self.assertEqual(pool.status()[0]["active_video_jobs"], 0)

    async def test_active_jobs_decrements_on_exception(self):
        """Slot is always released even when the body raises."""
        pool = self._pool(1, img_cap=2)
        acc = pool.account_ids()[0]

        async def failing_job():
            async with pool.image_slot(acc):
                raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            await failing_job()
        self.assertEqual(pool.status()[0]["active_image_jobs"], 0)

    async def test_has_image_capacity_reflects_semaphore(self):
        pool = self._pool(1, img_cap=1)
        acc = pool.account_ids()[0]
        self.assertTrue(pool.has_image_capacity(acc))
        entered = asyncio.Event()
        hold = asyncio.Event()

        async def occupy():
            async with pool.image_slot(acc):
                entered.set()
                await hold.wait()

        task = asyncio.create_task(occupy())
        await entered.wait()
        self.assertFalse(pool.has_image_capacity(acc))
        hold.set()
        await task
        self.assertTrue(pool.has_image_capacity(acc))

    async def test_media_bound_video_uses_source_account(self):
        """video_flow.py: extend/edit keeps source_video.account_id; check text assert."""
        # Moved to channels.telegram.video_flow (Phase 11).
        source = PROJECT_ROOT / "channels" / "telegram" / "video_flow.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn(
            "if source_video and source_video.account_id:",
            text,
        )
        self.assertIn("acc_id = source_video.account_id", text)

    async def test_image_routing_prefers_image_only_accounts(self):
        """pool.pick_for_image with prefer_image_only → prefers video_allowed=False."""
        accs = [FlowAccount(id="img_only", profile_dir="./p1"),
                FlowAccount(id="vid_cap", profile_dir="./p2")]
        pool = AccountPool(accs, None, default_image_capacity=2, default_video_capacity=1)
        pool.set_video_allowed("img_only", False)
        result = pool.pick_for_image(999, prefer_image_only=True)
        self.assertEqual(result, "img_only")

    async def test_video_routing_only_video_capable_accounts(self):
        """pool.pick_for_video returns None when all accounts are image-only."""
        accs = [FlowAccount(id="a1", profile_dir="./p1"),
                FlowAccount(id="a2", profile_dir="./p2")]
        pool = AccountPool(accs, None)
        pool.set_video_allowed("a1", False)
        pool.set_video_allowed("a2", False)
        self.assertIsNone(pool.pick_for_video(42))


class CapacityBotWiringTests(unittest.TestCase):
    """Text asserts: capacity wiring in flow_bot.py source."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (PROJECT_ROOT / "flow_bot.py").read_text(encoding="utf-8")
        cls.admin_status_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "admin_status.py"
        ).read_text(encoding="utf-8")

    def test_acc_capacity_env_vars_present(self):
        self.assertIn('ACC_IMAGE_CAPACITY', self.source)
        self.assertIn('ACC_VIDEO_CAPACITY', self.source)

    def test_account_pool_receives_capacity_defaults(self):
        self.assertIn('default_image_capacity=ACC_IMAGE_CAPACITY', self.source)
        self.assertIn('default_video_capacity=ACC_VIDEO_CAPACITY', self.source)

    def test_image_generation_wrapped_in_image_slot(self):
        gen_flow = (PROJECT_ROOT / "channels" / "telegram" / "generation_flow.py").read_text(encoding="utf-8")
        block = gen_flow[gen_flow.index("async def do_generate_and_send"):]
        self.assertIn("d.account_pool.image_slot(acc_id)", block)
        self.assertIn("async with d.account_pool.image_slot(acc_id):", block)

    def test_video_generation_wrapped_in_video_slot(self):
        # do_generate_and_send moved to channels.telegram.video_flow (Phase 11).
        block = (
            PROJECT_ROOT / "channels" / "telegram" / "video_flow.py"
        ).read_text(encoding="utf-8")
        self.assertIn("d.account_pool.video_slot(acc_id)", block)
        self.assertIn("async with d.account_pool.video_slot(acc_id):", block)

    def test_high_load_message_shown_when_capacity_full(self):
        gen_flow = (PROJECT_ROOT / "channels" / "telegram" / "generation_flow.py").read_text(encoding="utf-8")
        block = gen_flow[gen_flow.index("async def do_generate_and_send"):]
        self.assertIn('d.account_pool.has_image_capacity(acc_id)', block)
        self.assertIn('flow_copy.msg("high_load")', block)

    def test_status_command_shows_pool_capacity(self):
        start = self.admin_status_router_source.index("async def cmd_status")
        block = self.admin_status_router_source[start:start + 2200]
        self.assertIn("active_image_jobs", block)
        self.assertIn("active_video_jobs", block)
        self.assertIn("image_capacity", block)
        self.assertIn("video_capacity", block)


if __name__ == "__main__":
    unittest.main()
