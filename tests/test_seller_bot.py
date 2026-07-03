from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
import unittest

import flow_core
import flow_bot
import config.settings as _cfg
from generation import backend_service


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SELLER_PACKS = ("s_card", "s_5cards", "s_shop", "s_shopxl")


class SellerPackTests(unittest.TestCase):
    def test_consumer_pack_ids_exclude_seller_and_test(self) -> None:
        ids = flow_core.public_pack_ids()
        self.assertEqual(ids, ["trial", "small", "medium", "large", "xl"])
        for pid in SELLER_PACKS:
            self.assertNotIn(pid, ids)
        self.assertNotIn("test", ids)

    def test_seller_pack_ids(self) -> None:
        ids = flow_core.public_pack_ids(seller=True)
        self.assertEqual(ids, list(SELLER_PACKS))

    def test_test_pack_gated_by_include_test(self) -> None:
        self.assertNotIn("test", flow_core.public_pack_ids())
        self.assertNotIn("test", flow_core.public_pack_ids(seller=True))
        self.assertIn("test", flow_core.public_pack_ids(include_test=True))

    def test_seller_packs_have_credits_stars_and_rub(self) -> None:
        for pid in SELLER_PACKS:
            p = flow_core.pack(pid)
            self.assertIsNotNone(p, pid)
            self.assertGreater(p["credits"], 0)
            self.assertGreater(p["stars"], 0)
            self.assertIn(pid, flow_core.ROBOKASSA_PACK_AMOUNTS_RUB)

    def test_seller_packs_cheaper_per_credit_than_trial(self) -> None:
        # Volume reward: top seller pack must beat the trial rub/credit rate.
        trial_rate = float(flow_core.ROBOKASSA_PACK_AMOUNTS_RUB["trial"]) / flow_core.pack("trial")["credits"]
        shopxl_rate = float(flow_core.ROBOKASSA_PACK_AMOUNTS_RUB["s_shopxl"]) / flow_core.pack("s_shopxl")["credits"]
        self.assertLess(shopxl_rate, trial_rate)

    def test_opt_agency_pack_removed(self) -> None:
        self.assertIsNone(flow_core.pack("s_agency"))


class SellerMenuTests(unittest.TestCase):
    def setUp(self) -> None:
        self._was_seller = _cfg.IS_SELLER

    def tearDown(self) -> None:
        _cfg.IS_SELLER = self._was_seller

    def _callbacks(self, kb) -> list[str]:
        return [b.callback_data for row in kb.inline_keyboard for b in row]

    def test_marketplace_button_only_in_seller_mode(self) -> None:
        _cfg.IS_SELLER = True
        self.assertIn("m:mp", self._callbacks(flow_bot.main_menu_kb()))
        _cfg.IS_SELLER = False
        self.assertNotIn("m:mp", self._callbacks(flow_bot.main_menu_kb()))

    def test_seller_main_menu_is_marketplace_first(self) -> None:
        _cfg.IS_SELLER = True
        cb = self._callbacks(flow_bot.main_menu_kb())
        self.assertEqual(cb[0], "m:mp")               # marketplace first
        self.assertIn("m:balance", cb)
        for consumer_only in ("m:gen", "m:vid", "m:ideas", "m:myphoto"):
            self.assertNotIn(consumer_only, cb)
        # Consumer menu keeps its own buttons and has no marketplace entry.
        _cfg.IS_SELLER = False
        ccb = self._callbacks(flow_bot.main_menu_kb())
        self.assertIn("m:gen", ccb)
        self.assertNotIn("m:mp", ccb)

    def test_marketplace_stale_callback_detection(self) -> None:
        user_id = 909001
        flow_bot.wizard_state[user_id]["mp_active_msg_id"] = 200
        stale = SimpleNamespace(message=SimpleNamespace(message_id=199))
        current = SimpleNamespace(message=SimpleNamespace(message_id=200))
        unknown = SimpleNamespace(message=SimpleNamespace(message_id=0))
        self.assertTrue(flow_bot._mp_is_stale_callback(user_id, stale))
        self.assertFalse(flow_bot._mp_is_stale_callback(user_id, current))
        self.assertFalse(flow_bot._mp_is_stale_callback(user_id, unknown))
        flow_bot._mp_stamp_message(user_id, SimpleNamespace(message_id=201))
        self.assertEqual(flow_bot.wizard_state[user_id]["mp_active_msg_id"], 201)

    def test_marketplace_callbacks_are_stale_guarded(self) -> None:
        source = inspect.getsource(flow_bot.on_marketplace_action)
        self.assertIn("_mp_is_stale_callback(user_id, callback)", source)
        self.assertIn("_mp_reject_stale_callback(callback)", source)
        from channels.telegram.routers import menu as menu_router
        menu_source = inspect.getsource(menu_router)
        self.assertIn('elif data == "m:mp":', menu_source)
        self.assertIn("deps.mp_stamp_message(user_id, msg)", menu_source)
        action_source = inspect.getsource(flow_bot.on_image_action)
        self.assertIn("_mp_stamp_message(user_id, sent)", action_source)

    def test_seller_confirm_after_photo_upload_is_active_message(self) -> None:
        import asyncio

        class FakeCreditStore:
            def balance(self, user_id):  # noqa: ANN001
                return 100

        class FakeMessage:
            def __init__(self, user_id: int, file_id: str, message_id: int) -> None:
                self.from_user = SimpleNamespace(id=user_id, username="seller")
                self.photo = [SimpleNamespace(file_id=file_id)]
                self.caption = "red shoes"
                self.media_group_id = None
                self._message_id = message_id
                self.answers = []

            async def answer(self, text, **kwargs):  # noqa: ANN001
                self.answers.append((text, kwargs))
                return SimpleNamespace(message_id=self._message_id)

        async def run_case(await_key: str, pending_kind: str, message_id: int, extra: dict | None = None) -> None:
            user_id = 909100 + message_id
            st = flow_bot.wizard_state[user_id]
            st.clear()
            st.update({
                "await": await_key,
                "mp_platform": "wb",
                "mp_preset": "whitebg",
                "mp_pending_kind": pending_kind,
            })
            if extra:
                st.update(extra)
            msg = FakeMessage(user_id, f"file-{message_id}", message_id)
            await flow_bot.handle_photo(msg)
            self.assertEqual(st.get("mp_active_msg_id"), message_id)
            self.assertEqual(st.get("mp_pending_file_id"), f"file-{message_id}")
            self.assertIsNone(st.get("await"))
            self.assertEqual(len(msg.answers), 1)
            self.assertEqual(msg.answers[0][1].get("parse_mode"), "HTML")
            st.clear()

        was_seller = _cfg.IS_SELLER
        old_metrics = flow_bot.metrics
        old_credit_store = flow_bot.credit_store
        _cfg.IS_SELLER = True
        flow_bot.metrics = SimpleNamespace(
            log_event=lambda *args, **kwargs: None,
            upsert_user=lambda *args, **kwargs: None,
            get_seller_profile=lambda user_id: {},
        )
        flow_bot.credit_store = FakeCreditStore()
        try:
            asyncio.run(run_case("mp_photo", "photo", 701))
            asyncio.run(run_case("mp_series_photo", "series", 702, {"mp_series_count": 3}))
        finally:
            _cfg.IS_SELLER = was_seller
            flow_bot.metrics = old_metrics
            flow_bot.credit_store = old_credit_store

    def test_backend_dispatch_by_kind(self) -> None:
        import asyncio

        async def run() -> None:
            calls: list[str] = []
            orig_i2i = flow_bot._backend_generate_i2i
            orig_img = flow_bot._backend_generate_images
            orig_vid = flow_bot._backend_generate_video_ingredients

            async def fake_i2i(req):  # noqa: ANN001
                calls.append("i2i")
                return {"images": []}

            async def fake_img(req):  # noqa: ANN001
                calls.append("image")
                return {"images": []}

            async def fake_vid(req):  # noqa: ANN001
                calls.append("video")
                return {"videos": []}

            flow_bot._backend_generate_i2i = fake_i2i
            flow_bot._backend_generate_images = fake_img
            flow_bot._backend_generate_video_ingredients = fake_vid
            try:
                await flow_bot._backend_generate({"kind": "i2i"})
                await flow_bot._backend_generate({"kind": "video_ingredients"})
                await flow_bot._backend_generate({"kind": "image"})
                await flow_bot._backend_generate({})
            finally:
                flow_bot._backend_generate_i2i = orig_i2i
                flow_bot._backend_generate_images = orig_img
                flow_bot._backend_generate_video_ingredients = orig_vid
            self.assertEqual(calls, ["i2i", "video", "image", "image"])

        asyncio.run(run())

    def test_backend_service_is_platform_neutral(self) -> None:
        src = inspect.getsource(backend_service)
        self.assertNotIn("aiogram", src)
        self.assertNotIn("types.Message", src)
        self.assertNotIn("telegram.", src.lower())
        self.assertIn("async def generate_images", src)
        self.assertIn("async def generate_i2i", src)
        self.assertIn("async def generate_video_ingredients", src)

    def test_backend_i2i_uses_account_failover(self) -> None:
        src = inspect.getsource(backend_service.generate_i2i)
        self.assertIn("for attempt in range(2):", src)
        self.assertIn("prefer_image_only=True", src)
        self.assertIn("exclude=tried if tried else None", src)
        self.assertIn("upload_image(", src)
        self.assertIn("data, filename=", src)
        self.assertIn("allow_browser_fallback=False", src)

    def test_backend_video_ingredients_returns_video_bytes(self) -> None:
        src = inspect.getsource(backend_service.generate_video_ingredients)
        self.assertIn('"video_ingredients"', inspect.getsource(flow_bot._backend_generate))
        self.assertIn("video_b64", src)
        self.assertIn("fetch_video_bytes(media_id)", src)
        self.assertIn("reference_sources=[source]", src)
        self.assertIn("deps.account_for_video(user_id)", src)

    def test_web_server_allows_large_internal_media_payloads(self) -> None:
        src = inspect.getsource(flow_bot._start_web_server)
        self.assertIn("WEB_CLIENT_MAX_SIZE", src)
        self.assertIn("client_max_size=client_max_size", src)

    def test_seller_marketplace_animate_is_backend_wired(self) -> None:
        source = inspect.getsource(flow_bot.on_marketplace_action)
        self.assertIn('st["await"] = "mp_video_photo"', source)
        self.assertIn("_mp_video_request_text(plat)", source)
        self.assertNotIn("Видео для карточек скоро", source)
        photo_source = inspect.getsource(flow_bot.handle_photo)
        self.assertIn('st.get("await") == "mp_video_photo"', photo_source)
        self.assertIn("_seller_video_from_photo(", photo_source)

    def test_platform_keyboard(self) -> None:
        cb = self._callbacks(flow_bot.mp_root_kb())
        self.assertEqual(
            [c for c in cb if c.startswith("mp:plat:")],
            ["mp:plat:wb", "mp:plat:ozon", "mp:plat:ym"],
        )

    def test_jobs_keyboard_has_core_jobs(self) -> None:
        cb = self._callbacks(flow_bot.mp_jobs_kb("wb"))
        for job in ("info", "whitebg", "model"):
            self.assertIn(f"mp:job:{job}", cb)
        self.assertIn("mp:series", cb)
        self.assertIn("mp:more", cb)
        for hidden in (
            "mp:job:cover",
            "mp:job:bg",
            "mp:job:animate",
            "mp:brandkit",
            "mp:niche",
            "mp:projects",
            "mp:tips",
        ):
            self.assertNotIn(hidden, cb)
        self.assertNotIn("mp:done4you", cb)  # «Сделайте за меня (под ключ)» убрана
        # Platform is now chosen at the settings step, not upfront, so the jobs
        # screen is the marketplace entry and goes back to the main menu.
        self.assertIn("m:menu", cb)
        labels = [b.text for row in flow_bot.mp_jobs_kb("wb").inline_keyboard for b in row]
        self.assertEqual(
            labels[:4],
            [
                "✨ Готовая карточка с инфографикой",
                "📸 Белый фон для каталога",
                "🧍 Товар на модели / в сцене",
                "🧩 Серия слайдов",
            ],
        )

    def test_more_keyboard_keeps_secondary_seller_actions(self) -> None:
        cb = self._callbacks(flow_bot.mp_more_kb("wb"))
        for expected in (
            "mp:job:cover",
            "mp:job:bg",
            "mp:job:animate",
            "mp:brandkit",
            "mp:niche",
            "mp:projects",
            "mp:tips",
            "m:mp",
            "m:menu",
        ):
            self.assertIn(expected, cb)
        text = flow_bot._mp_more_text("wb")
        self.assertIn("Дополнительные задачи", text)
        self.assertIn("основным задачам", text)

    def test_seller_start_goes_directly_to_jobs(self) -> None:
        source = inspect.getsource(flow_bot.cmd_start)
        seller_branch = source[source.index("if _cfg.IS_SELLER:"):source.index("if _referral_welcome_bonus")]
        self.assertIn("mp_jobs_kb", seller_branch)
        self.assertIn("_mp_stamp_message", seller_branch)
        self.assertNotIn("show_main_menu(message, user_id=user_id)", seller_branch)

    def test_seller_copy_is_direct_and_no_hidden_done4you_promise(self) -> None:
        welcome = flow_bot.flow_copy.msg("welcome_seller")
        help_text = flow_bot.flow_copy.msg("seller_help")
        self.assertIn("Выбери, что нужно", welcome)
        self.assertIn("«Создать»", welcome)
        self.assertNotIn("Сделайте за меня", welcome)
        self.assertNotIn("Google", welcome + help_text)
        self.assertNotIn("Flow", welcome + help_text)
        self.assertNotIn("captcha", (welcome + help_text).lower())

    def test_photo_settings_keyboard_has_platform_picker(self) -> None:
        kb = flow_bot._mp_photo_settings_kb("wb")
        cb = self._callbacks(kb)
        self.assertIn("mp:setplat:wb", cb)
        self.assertIn("mp:setplat:ozon", cb)
        self.assertIn("mp:setplat:ym", cb)
        labels = " ".join(b.text for row in kb.inline_keyboard for b in row)
        self.assertIn("WB 3:4", labels)
        self.assertIn("Ozon 3:4", labels)
        self.assertIn("ЯМ 1:1", labels)

    def test_marketplace_platform_formats_are_real(self) -> None:
        self.assertEqual(flow_bot._MP_PLATFORM_FMT, {"wb": "f34", "ozon": "f34", "ym": "sq"})
        self.assertEqual(flow_bot._mp_platform_aspect("wb"), "portrait_34")
        self.assertEqual(flow_bot._mp_platform_aspect("ozon"), "portrait_34")
        self.assertEqual(flow_bot._mp_platform_aspect("ym"), "square")
        self.assertIn("1080x1440", flow_bot._mp_platform_format_label("wb"))
        self.assertIn("1000x1000", flow_bot._mp_platform_format_label("ym"))

    def test_mp_confirm_screen_shows_price_brand_and_create(self) -> None:
        flow_bot.wizard_state[5550001].update(
            {"mp_platform": "wb", "mp_preset": "whitebg", "mp_pending_kind": "photo"}
        )
        text, kb = flow_bot._mp_confirm_screen(5550001)
        self.assertIn("кр", text)                  # цена показана
        self.assertIn("Бренд-кит", text)            # бренд-кит/ниша на экране решения
        self.assertIn("Ниша", text)
        self.assertIn("3:4", text)
        cb = self._callbacks(kb)
        self.assertIn("mp:create", cb)              # есть явная кнопка «Создать»
        # «Создать» подписана ценой действия edit.
        labels = [b.text for row in kb.inline_keyboard for b in row]
        self.assertTrue(any("Создать" in lbl for lbl in labels))

    def test_mp_confirm_screen_uses_platform_format(self) -> None:
        flow_bot.wizard_state[5550002].update(
            {"mp_platform": "ym", "mp_preset": "cover", "mp_pending_kind": "photo"}
        )
        text, _kb = flow_bot._mp_confirm_screen(5550002)
        self.assertIn("Яндекс Маркет", text)
        self.assertIn("1:1", text)
        self.assertIn("1000x1000", text)

    def test_series_keyboard_has_bundle_prices(self) -> None:
        kb = flow_bot.mp_series_kb("wb")
        cb = self._callbacks(kb)
        self.assertEqual(
            [c for c in cb if c.startswith("mp:series:")],
            ["mp:series:3", "mp:series:5", "mp:series:8"],
        )
        text = "\n".join(b.text for row in kb.inline_keyboard for b in row)
        self.assertIn("30 кр", text)
        self.assertIn("45 кр", text)
        self.assertIn("70 кр", text)

    def test_job_seeds_cover_image_jobs(self) -> None:
        for job in ("whitebg", "info", "model", "cover", "bg"):
            self.assertIn(job, flow_bot._MP_JOB_SEED)
            self.assertTrue(flow_bot._MP_JOB_SEED[job])

    def test_marketplace_image_jobs_wait_for_product_photo(self) -> None:
        source = inspect.getsource(flow_bot.on_marketplace_action)
        self.assertIn('if job not in _MP_PRODUCT_PHOTO_JOBS:', source)
        self.assertIn('st["await"] = "mp_photo"', source)
        self.assertIn('st["edit_fmt"] = _mp_platform_fmt(plat)', source)
        self.assertIn("_mp_photo_request_text(plat, job)", source)
        self.assertNotIn("await show_wizard(msg", source)

    def test_marketplace_instruction_uses_uploaded_photo(self) -> None:
        prompt = flow_bot._mp_job_instruction("whitebg", "wb", "красные ботинки")
        self.assertIn("загруженное фото", prompt)
        self.assertIn("Wildberries", prompt)
        self.assertIn("красные ботинки", prompt)
        self.assertIn("3:4", prompt)
        self.assertIn("верхнюю зону", prompt)

    def test_marketplace_instruction_uses_platform_guidance(self) -> None:
        ozon_prompt = flow_bot._mp_job_instruction("whitebg", "ozon")
        ym_prompt = flow_bot._mp_job_instruction("cover", "ym")
        self.assertIn("Ozon", ozon_prompt)
        self.assertIn("светлая композиция", ozon_prompt)
        self.assertIn("Яндекс Маркет", ym_prompt)
        self.assertIn("1:1", ym_prompt)
        self.assertIn("товар по центру", ym_prompt)

    def test_marketplace_series_waits_for_product_photo(self) -> None:
        source = inspect.getsource(flow_bot.on_marketplace_action)
        self.assertIn('if data == "mp:series":', source)
        self.assertIn('if data.startswith("mp:series:"):', source)
        self.assertIn('st["await"] = "mp_series_photo"', source)
        self.assertIn('st["mp_series_count"] = count', source)
        self.assertIn("_mp_series_request_text(plat, count)", source)
        self.assertIn('"mp_job"', source)

    def test_marketplace_series_prompt_uses_uploaded_photo(self) -> None:
        prompt = flow_bot._mp_series_prompt("ozon", 5, "зелёная бутылка")
        self.assertIn("5", prompt)
        self.assertIn("Ozon", prompt)
        self.assertIn("загруженное фото", prompt)
        self.assertIn("зелёная бутылка", prompt)
        self.assertIn("3:4", prompt)

    def test_marketplace_series_prompt_uses_platform_format(self) -> None:
        prompt = flow_bot._mp_series_prompt("ym", 3)
        self.assertIn("Яндекс Маркет", prompt)
        self.assertIn("1:1", prompt)
        self.assertIn("1000x1000", prompt)
        self.assertIn("товар по центру", prompt)

    def test_marketplace_prompts_include_brand_kit_when_present(self) -> None:
        prompt = flow_bot._mp_job_instruction("cover", "wb", brand_kit="чёрный и золото")
        self.assertIn("Бренд-кит продавца", prompt)
        self.assertIn("чёрный и золото", prompt)
        series_prompt = flow_bot._mp_series_prompt("wb", 3, brand_kit="минимализм")
        self.assertIn("Бренд-кит продавца", series_prompt)
        self.assertIn("минимализм", series_prompt)

    def test_marketplace_prompts_include_niche_when_present(self) -> None:
        prompt = flow_bot._mp_job_instruction("model", "wb", niche="clothes")
        self.assertIn("Ниша товара", prompt)
        self.assertIn("Одежда", prompt)
        self.assertIn("посадку", prompt)
        series_prompt = flow_bot._mp_series_prompt("wb", 3, niche="electronics")
        self.assertIn("Ниша товара", series_prompt)
        self.assertIn("Электроника", series_prompt)

    def test_marketplace_series_photo_runs_i2i_bundle_action(self) -> None:
        source = inspect.getsource(flow_bot.handle_photo)
        self.assertIn('if st.get("await") == "mp_series_photo":', source)
        self.assertIn("niche=_mp_niche(user_id)", source)
        self.assertIn('num_images=count', source)
        self.assertIn('action="mp_series"', source)

    def test_done4you_sets_support_brief_state(self) -> None:
        source = inspect.getsource(flow_bot.on_marketplace_action)
        self.assertIn('if data == "mp:done4you":', source)
        self.assertIn('st["support_await"] = True', source)
        self.assertIn('st["support_kind"] = "mp_done4you"', source)
        self.assertIn('"mp_done4you_open"', source)

    def test_done4you_brief_is_tagged_ticket(self) -> None:
        source = inspect.getsource(flow_bot.handle_plain_text)
        self.assertIn('support_kind = st.pop("support_kind", "support")', source)
        self.assertIn('if support_kind == "mp_done4you":', source)
        self.assertIn("Заявка под ключ", source)
        self.assertIn('"mp_done4you_submitted"', source)

    def test_sku_projects_menu_and_result_action(self) -> None:
        source = inspect.getsource(flow_bot.on_marketplace_action)
        self.assertIn('if data == "mp:projects":', source)
        self.assertIn("_show_sku_projects", source)
        self.assertIn('data.startswith("mp:sku:open:")', source)
        self.assertIn('if data == "mp:sku:addlast":', source)
        self.assertIn('if data == "mp:sku:rename":', source)
        self.assertIn('if data == "mp:sku:delete"', source)
        self.assertIn("metrics.rename_seller_sku_project", inspect.getsource(flow_bot.handle_plain_text))
        self.assertIn("metrics.delete_seller_sku_project", source)
        action_source = inspect.getsource(flow_bot.on_image_action)
        self.assertIn('elif action == "skuadd":', action_source)
        self.assertIn('st["mp_sku_pending"]', action_source)
        self.assertIn("_mp_sku_choice_kb(user_id)", action_source)

    def test_sku_projects_keyboard_opens_items_and_creates_new(self) -> None:
        projects = [
            {"sku": "SKU-1 Red Shoes", "items": 1, "platform": "wb", "updated_at": "2026-06-21 10:00"},
            {"sku": "SKU-2 Empty", "items": 0, "platform": "ym", "updated_at": "2026-06-21 11:00"},
        ]
        kb = flow_bot._mp_sku_projects_kb(777001, projects)
        cb = self._callbacks(kb)
        self.assertIn("mp:sku:open:0", cb)
        self.assertIn("mp:sku:open:1", cb)
        self.assertIn("mp:sku:new", cb)
        self.assertEqual(flow_bot.wizard_state[777001]["mp_sku_project_choices"], ["SKU-1 Red Shoes", "SKU-2 Empty"])
        text = flow_bot._mp_sku_projects_text(777001, projects)
        self.assertIn("рабочее пространство", text)
        self.assertIn("0 слайдов", text)

    def test_sku_open_screen_has_workspace_actions(self) -> None:
        orig_get = getattr(flow_bot.metrics, "get_seller_sku_project", None)

        def fake_get(user_id, sku):  # noqa: ANN001
            return {
                "sku": sku,
                "items": 3,
                "platform": "ozon",
                "updated_at": "2026-06-21 12:00",
                "latest_prompt": "чистый белый фон",
            }

        flow_bot.metrics.get_seller_sku_project = fake_get
        try:
            text = flow_bot._mp_sku_open_text(777002, "SKU-OZON")
            kb = flow_bot._mp_sku_open_kb()
        finally:
            if orig_get is None:
                delattr(flow_bot.metrics, "get_seller_sku_project")
            else:
                flow_bot.metrics.get_seller_sku_project = orig_get
        self.assertIn("SKU-OZON", text)
        self.assertIn("3 слайда", text)
        self.assertIn("Ozon", text)
        cb = self._callbacks(kb)
        self.assertIn("mp:sku:addlast", cb)
        self.assertIn("mp:sku:rename", cb)
        self.assertIn("mp:sku:delete", cb)

    def test_seller_image_keyboard_adds_export_and_sku_buttons(self) -> None:
        cb = self._callbacks(flow_bot._seller_image_keyboard("tok123"))
        self.assertIn("mpe:tok123", cb)
        self.assertIn("sku:tok123", cb)
        normal_cb = self._callbacks(flow_bot._image_keyboard("tok123"))
        self.assertNotIn("mpe:tok123", normal_cb)
        self.assertNotIn("sku:tok123", normal_cb)

    def test_seller_marketplace_export_action_uses_document_helper(self) -> None:
        action_source = inspect.getsource(flow_bot.on_image_action)
        self.assertIn('elif action == "mpexport":', action_source)
        self.assertIn("marketplace_export=True", action_source)
        ref = flow_core.ImageRef(
            user_id=1,
            project_id="p",
            source={"mediaId": "mediaabcdef123456"},
            platform="wb",
            aspect_ratio="portrait_34",
        )
        filename = flow_bot._marketplace_export_filename(ref, b"\x89PNG\r\n\x1a\nrest")
        self.assertTrue(filename.startswith("photozhab_wildberries_3x4_"))
        self.assertTrue(filename.endswith(".png"))
        self.assertIn("Wildberries", flow_bot._marketplace_export_caption(ref))
        square_ref = flow_core.ImageRef(
            user_id=1,
            project_id="p",
            source={"mediaId": "mediaabcdef123456"},
            platform="ym",
            aspect_ratio="square",
        )
        square_filename = flow_bot._marketplace_export_filename(square_ref, b"\x89PNG\r\n\x1a\nrest")
        self.assertTrue(square_filename.startswith("photozhab_yandex_market_1x1_"))

    def test_seller_history_uses_flow_jobs(self) -> None:
        source = inspect.getsource(flow_bot._show_prompt_history)
        self.assertIn("if _cfg.IS_SELLER", source)
        self.assertIn("_seller_history_text(user_id)", source)
        orig_history = getattr(flow_bot.metrics, "get_seller_history", None)
        orig_gallery = getattr(flow_bot.metrics, "get_gallery", None)

        def fake_history(user_id, limit=10):  # noqa: ANN001
            return [
                {
                    "created_at": "2026-06-21 12:34:00",
                    "operation_type": "mp_series",
                    "status": "success",
                    "bot_credits_charged": 30,
                    "refund_amount": 0,
                    "mp_source": "ym:series:3",
                },
                {
                    "created_at": "2026-06-21 12:00:00",
                    "operation_type": "edit",
                    "status": "fail",
                    "error_type": "backend_failed",
                    "bot_credits_charged": 0,
                    "refund_amount": 15,
                    "mp_source": "wb:whitebg",
                },
            ]

        flow_bot.metrics.get_seller_history = fake_history
        flow_bot.metrics.get_gallery = lambda user_id, limit=5: []
        try:
            text = flow_bot._seller_history_text(777003)
        finally:
            if orig_history is None:
                delattr(flow_bot.metrics, "get_seller_history")
            else:
                flow_bot.metrics.get_seller_history = orig_history
            if orig_gallery is None:
                delattr(flow_bot.metrics, "get_gallery")
            else:
                flow_bot.metrics.get_gallery = orig_gallery
        self.assertIn("Яндекс Маркет", text)
        self.assertIn("серия · 3 слайда", text)
        self.assertIn("✅ готово", text)
        self.assertIn("списано 30 кр", text)
        self.assertIn("Wildberries", text)
        self.assertIn("❌ ошибка", text)

    def test_seller_history_falls_back_to_gallery(self) -> None:
        orig_history = getattr(flow_bot.metrics, "get_seller_history", None)
        orig_gallery = getattr(flow_bot.metrics, "get_gallery", None)
        flow_bot.metrics.get_seller_history = lambda user_id, limit=10: []
        flow_bot.metrics.get_gallery = lambda user_id, limit=5: [
            {"created_at": "2026-06-21 13:00:00", "prompt": "карточка товара"}
        ]
        try:
            text = flow_bot._seller_history_text(777004)
        finally:
            if orig_history is None:
                delattr(flow_bot.metrics, "get_seller_history")
            else:
                flow_bot.metrics.get_seller_history = orig_history
            if orig_gallery is None:
                delattr(flow_bot.metrics, "get_gallery")
            else:
                flow_bot.metrics.get_gallery = orig_gallery
        self.assertIn("Готовые работы уже есть", text)
        self.assertIn("карточка товара", text)

    def test_brandkit_sets_profile_state(self) -> None:
        source = inspect.getsource(flow_bot.on_marketplace_action)
        self.assertIn('if data == "mp:brandkit":', source)
        self.assertIn('st["await"] = "mp_brandkit"', source)
        self.assertIn("_mp_brandkit_text(user_id)", source)

    def test_niche_presets_save_profile(self) -> None:
        cb = self._callbacks(flow_bot._mp_niche_kb())
        self.assertEqual(
            [c for c in cb if c.startswith("mp:niche:")],
            ["mp:niche:clothes", "mp:niche:beauty", "mp:niche:electronics", "mp:niche:kids", "mp:niche:food"],
        )
        source = inspect.getsource(flow_bot.on_marketplace_action)
        self.assertIn('if data == "mp:niche":', source)
        self.assertIn('if data.startswith("mp:niche:"):', source)
        self.assertIn("metrics.save_seller_profile(user_id, niche=niche_id)", source)
        self.assertIn('"mp_niche_saved"', source)

    def test_seller_service_templates_are_wired_for_separate_env(self) -> None:
        root = PROJECT_ROOT
        flow_source = (root / "flow_bot.py").read_text(encoding="utf-8")
        deploy_script = (root / "deploy.sh").read_text(encoding="utf-8")
        seller_unit = (root / "deploy/systemd/geminifree-seller-bot.service").read_text(encoding="utf-8")
        seller_runner = (root / "deploy/bin/geminifree-seller-bot-run").read_text(encoding="utf-8")
        seller_env = (root / "deploy/examples/seller.env.example").read_text(encoding="utf-8")
        gitignore = (root / ".gitignore").read_text(encoding="utf-8")

        self.assertIn('ENV_FILE = os.getenv("ENV_FILE", ".env") or ".env"', flow_source)
        self.assertIn("load_dotenv(ENV_FILE)", flow_source)
        self.assertIn("geminifree-seller-bot", deploy_script)
        self.assertIn("Skipping $unit (unit is not installed)", deploy_script)
        self.assertIn("Skipping $unit (missing $required_env)", deploy_script)
        self.assertIn("restart_if_installed geminifree-seller-bot /opt/geminifree/.env.seller", deploy_script)
        self.assertIn("ENV_FILE=/opt/geminifree/.env.seller", seller_unit)
        self.assertIn("/usr/local/bin/geminifree-seller-bot-run", seller_unit)
        self.assertIn("/opt/geminifree/.env.seller", seller_runner)
        self.assertIn("BOT_MODE=seller", seller_env)
        self.assertIn("USER_CREDITS_FILE=user_credits_seller.json", seller_env)
        self.assertIn("ROBOKASSA_WEB_PORT=8082", seller_env)
        self.assertIn("ROBOKASSA_SCOPE=seller", seller_env)
        self.assertIn("ROBOKASSA_SELLER_RESULT_URL=http://127.0.0.1:8082/robokassa/result", seller_env)
        self.assertIn("SBP_PAYMENT_ENABLED=1", seller_env)
        self.assertIn("user_credits_*.json", gitignore)


if __name__ == "__main__":
    unittest.main()
