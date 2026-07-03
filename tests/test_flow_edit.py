from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import flow_core
import flow_copy
from flow_core import (
    ImageRef,
    ImageRegistry,
    UserProjectStore,
    build_generation_payload,
    build_image_inputs,
    edit_callback_data,
    parse_edit_callback,
    parse_result,
    result_pairs,
    aspect_code,
    image_model_key,
    image_model_extra,
    IMAGE_MODELS,
    DEFAULT_IMAGE_MODEL,
    build_upload_image_payload,
    parse_upload_image_response,
    IMAGE_UPLOAD_ENDPOINT,
    build_upsample_payload,
    parse_upsample_response,
    IMAGE_UPSAMPLE_ENDPOINT,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

_PR2A_PROVIDER_SOURCE = (
    (PROJECT_ROOT / "flow_provider" / "client.py").read_text(encoding="utf-8")
    + "\n"
    + (PROJECT_ROOT / "flow_provider" / "runtime_config.py").read_text(encoding="utf-8")
)


class ResultParsingTests(unittest.TestCase):
    def test_parse_result_matches_both_response_shapes(self) -> None:
        data = {
            "responses": [
                {"generatedImage": {"mediaStoreUri": "media://a", "fifeUrl": "http://a"}},
                {"imageOutput": {"uri": "media://b"}},
            ],
            "media": [
                {"image": {"generatedImage": {"fifeUrl": "http://c"}}},
            ],
        }
        # URL precedence is unchanged from the previous inline parser.
        self.assertEqual(parse_result(data), ["media://a", "media://b", "http://c"])

    def test_result_pairs_preserves_raw_image_dict_for_editing(self) -> None:
        data = {"responses": [{"generatedImage": {"mediaStoreUri": "media://a"}}]}
        pairs = result_pairs(data)
        self.assertEqual(len(pairs), 1)
        url, img = pairs[0]
        self.assertEqual(url, "media://a")
        self.assertEqual(img["mediaStoreUri"], "media://a")

    def test_parsers_tolerate_garbage_without_raising(self) -> None:
        for junk in ({}, {"responses": None}, {"media": [None, 1, "x"]}, {"responses": ["x"]}):
            self.assertEqual(parse_result(junk), [])
            self.assertEqual(result_pairs(junk), [])


class PayloadTests(unittest.TestCase):
    def test_generation_payload_has_empty_image_inputs(self) -> None:
        payload = build_generation_payload(
            prompt="cat",
            project_id="proj-1",
            captcha_token="tok",
            aspect="landscape",
            num_images=3,
            seed=100,
            session_id=";123",
        )
        self.assertEqual(payload["clientContext"]["projectId"], "proj-1")
        self.assertEqual(payload["clientContext"]["recaptchaContext"]["token"], "tok")
        self.assertEqual(len(payload["requests"]), 3)
        self.assertEqual(payload["requests"][0]["seed"], 100)
        self.assertEqual(payload["requests"][2]["seed"], 102)
        self.assertEqual(payload["requests"][0]["imageModelName"], "GEM_PIX_2")
        self.assertEqual(
            payload["requests"][0]["imageAspectRatio"], "IMAGE_ASPECT_RATIO_LANDSCAPE"
        )
        for req in payload["requests"]:
            self.assertEqual(req["imageInputs"], [])

    def test_new_aspect_ratios_verified_enums(self) -> None:
        # Verified from a real batchGenerateImages capture (2026-06-09).
        self.assertEqual(aspect_code("4:3"), "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE")
        self.assertEqual(aspect_code("3:4"), "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR")
        self.assertEqual(aspect_code("landscape_43"), "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE")
        self.assertEqual(aspect_code("portrait_34"), "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR")
        # Existing ones unchanged.
        self.assertEqual(aspect_code("1:1"), "IMAGE_ASPECT_RATIO_SQUARE")
        # Unknown still falls back to landscape.
        self.assertEqual(aspect_code("weird"), "IMAGE_ASPECT_RATIO_LANDSCAPE")

    def test_image_model_catalog_and_keys(self) -> None:
        # Verified imageModelName strings.
        self.assertEqual(image_model_key("nb2"), "GEM_PIX_2")
        self.assertEqual(image_model_key("nbpro"), "NARWHAL")
        # Unknown / default falls back to the historical default model.
        self.assertEqual(image_model_key("???"), "GEM_PIX_2")
        self.assertEqual(image_model_key(DEFAULT_IMAGE_MODEL), "GEM_PIX_2")
        # Pro carries a +5 retail surcharge; default model is free of it.
        self.assertEqual(image_model_extra("nb2"), 0)
        self.assertEqual(image_model_extra("nbpro"), 5)
        self.assertEqual(image_model_extra("unknown"), 0)

    def test_generation_payload_threads_model_and_aspect(self) -> None:
        payload = build_generation_payload(
            prompt="cat",
            project_id="proj-1",
            captcha_token="tok",
            aspect="3:4",
            num_images=2,
            seed=5,
            session_id=";1",
            image_model="nbpro",
        )
        for req in payload["requests"]:
            self.assertEqual(req["imageModelName"], "NARWHAL")
            self.assertEqual(req["imageAspectRatio"], "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR")
        # Default call still uses the default model (back-compat).
        default = build_generation_payload(
            prompt="x", project_id="p", captcha_token="t",
            aspect="landscape", num_images=1, seed=1, session_id=";1",
        )
        self.assertEqual(default["requests"][0]["imageModelName"], "GEM_PIX_2")

    def test_upsample_payload_matches_capture(self) -> None:
        # Verified from a real flow/upsampleImage capture (2026-06-09).
        self.assertEqual(
            IMAGE_UPSAMPLE_ENDPOINT,
            "https://aisandbox-pa.googleapis.com/v1/flow/upsampleImage",
        )
        p = build_upsample_payload(
            media_id="1f0d8870", project_id="proj", captcha_token="tok", session_id=";9",
        )
        self.assertEqual(p["mediaId"], "1f0d8870")
        self.assertEqual(p["targetResolution"], "UPSAMPLE_IMAGE_RESOLUTION_2K")
        self.assertEqual(p["clientContext"]["projectId"], "proj")
        self.assertEqual(p["clientContext"]["tool"], "PINHOLE")
        self.assertEqual(p["clientContext"]["recaptchaContext"]["token"], "tok")

    def test_parse_upsample_response(self) -> None:
        self.assertEqual(parse_upsample_response({"encodedImage": "QUJD"}), "QUJD")
        self.assertIsNone(parse_upsample_response({}))
        self.assertIsNone(parse_upsample_response({"encodedImage": ""}))
        self.assertIsNone(parse_upsample_response("nope"))

    def test_build_image_inputs_uses_known_default_shape(self) -> None:
        # Derived from a REAL intercepted edit: BASE_IMAGE + name=mediaId.
        out = build_image_inputs({"mediaId": "56999c74-e7d4-4395-b462-78568cb547e1"})
        self.assertEqual(
            out,
            [
                {
                    "imageInputType": "IMAGE_INPUT_TYPE_BASE_IMAGE",
                    "name": "56999c74-e7d4-4395-b462-78568cb547e1",
                }
            ],
        )

    def test_build_image_inputs_empty_when_no_identifier(self) -> None:
        self.assertEqual(build_image_inputs({}), [])
        self.assertEqual(build_image_inputs({"foo": "bar"}), [])

    def test_learned_capture_overrides_default(self) -> None:
        capture = {
            "resolved": True,
            "ref_path": ["name"],
            "extract": "whole",
            "ref_source_key": "mediaId",
            "template": {"imageInputType": "OTHER", "name": flow_core.REF_PLACEHOLDER},
        }
        out = build_image_inputs({"mediaId": "X"}, capture)
        self.assertEqual(out, [{"imageInputType": "OTHER", "name": "X"}])

    def test_build_image_inputs_uses_captured_template(self) -> None:
        capture = {
            "resolved": True,
            "ref_path": ["media", "name"],
            "ref_source_key": "mediaStoreUri",
            "template": {"media": {"name": flow_core.REF_PLACEHOLDER}, "role": "RAW"},
        }
        inputs = build_image_inputs({"mediaStoreUri": "media://src"}, capture)
        self.assertEqual(inputs, [{"media": {"name": "media://src"}, "role": "RAW"}])

    def test_capture_template_is_not_mutated_across_builds(self) -> None:
        capture = {
            "resolved": True,
            "ref_path": ["name"],
            "ref_source_key": "mediaStoreUri",
            "template": {"name": flow_core.REF_PLACEHOLDER},
        }
        a = build_image_inputs({"mediaStoreUri": "A"}, capture)
        b = build_image_inputs({"mediaStoreUri": "B"}, capture)
        self.assertEqual(a, [{"name": "A"}])
        self.assertEqual(b, [{"name": "B"}])
        self.assertEqual(capture["template"], {"name": flow_core.REF_PLACEHOLDER})


class CaptureLearningTests(unittest.TestCase):
    def test_build_capture_locates_ref_slot_by_matching_recent_image(self) -> None:
        # Real edit request references the image deep inside a nested field.
        image_inputs = [
            {"media": {"name": "media-store://abc"}, "category": "MEDIA_CATEGORY_RAW"}
        ]
        recent = [{"mediaStoreUri": "media-store://abc", "fifeUrl": "http://x"}]
        cap = flow_core.build_capture_from_inputs(image_inputs, recent)
        self.assertTrue(cap["resolved"])
        self.assertEqual(cap["ref_path"], ["media", "name"])
        self.assertEqual(cap["ref_source_key"], "mediaStoreUri")
        # The matched value is replaced by the placeholder in the stored template.
        self.assertEqual(cap["template"]["media"]["name"], flow_core.REF_PLACEHOLDER)
        self.assertEqual(cap["template"]["category"], "MEDIA_CATEGORY_RAW")

        # Round-trip: the learned capture reproduces a usable element.
        built = build_image_inputs({"mediaStoreUri": "media-store://NEW"}, cap)
        self.assertEqual(built[0]["media"]["name"], "media-store://NEW")
        self.assertEqual(built[0]["category"], "MEDIA_CATEGORY_RAW")

    def test_build_capture_unresolved_when_no_match(self) -> None:
        cap = flow_core.build_capture_from_inputs(
            [{"unknownRef": "something-we-never-saw-at-all"}],
            [{"mediaStoreUri": "media-store://abc"}],
        )
        self.assertFalse(cap["resolved"])
        self.assertIn("schema", cap)
        # An unresolved capture falls back to the known-good default shape.
        self.assertEqual(
            build_image_inputs({"mediaId": "abc"}, cap),
            [{"imageInputType": "IMAGE_INPUT_TYPE_BASE_IMAGE", "name": "abc"}],
        )

    def test_build_capture_matches_bare_id_embedded_in_stored_url(self) -> None:
        # Observed real case: imageInputs references a bare 36-char id via "name",
        # while the bot stores the longer mediaStoreUri that contains that id.
        bare_id = "10cdaa18-0d3c-4161-bbd3-9eba58bb4871"
        stored = f"media-store:projects/p/locations/us/media/{bare_id}"
        image_inputs = [
            {"imageInputType": "IMAGE_INPUT_TYPE_GENERATED_X", "name": bare_id}
        ]
        recent = [{"mediaStoreUri": stored, "fifeUrl": "http://cdn/x"}]
        cap = flow_core.build_capture_from_inputs(image_inputs, recent)

        self.assertTrue(cap["resolved"])
        self.assertEqual(cap["extract"], "substring")
        self.assertEqual(cap["ref_path"], ["name"])
        self.assertEqual(cap["ref_source_key"], "mediaStoreUri")
        # The enum constant is preserved verbatim in the template.
        self.assertEqual(
            cap["template"]["imageInputType"], "IMAGE_INPUT_TYPE_GENERATED_X"
        )
        self.assertEqual(cap["template"]["name"], flow_core.REF_PLACEHOLDER)

        # A NEW image's bare id is correctly extracted from its stored URL.
        new_id = "99999999-0000-1111-2222-333344445555"
        new_stored = f"media-store:projects/p/locations/us/media/{new_id}"
        built = build_image_inputs({"mediaStoreUri": new_stored}, cap)
        self.assertEqual(built[0]["name"], new_id)
        self.assertEqual(built[0]["imageInputType"], "IMAGE_INPUT_TYPE_GENERATED_X")

    def test_describe_schema_emits_no_values(self) -> None:
        # A clearly-fake value that must never appear in the schema output.
        value_like = "VALUE-THAT-MUST-NOT-LEAK-0123456789"
        schema = flow_core.describe_schema(
            [{"media": {"name": value_like}, "n": 3, "flag": True}]
        )
        text = repr(schema)
        self.assertNotIn(value_like, text)
        self.assertIn("str[len=", text)

    def test_edit_capture_persists_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "edit_capture.json"
            self.assertIsNone(flow_core.load_edit_capture(path))
            cap = {"resolved": True, "ref_path": ["a"], "template": {"a": "X"}}
            flow_core.save_edit_capture(path, cap)
            self.assertEqual(flow_core.load_edit_capture(path), cap)

    def test_path_helpers_handle_lists_and_dicts(self) -> None:
        node = {"items": [{"ref": "v0"}, {"ref": "v1"}]}
        self.assertEqual(flow_core.get_path(node, ["items", 1, "ref"]), "v1")
        flow_core.set_path(node, ["items", 0, "ref"], "changed")
        self.assertEqual(node["items"][0]["ref"], "changed")


class CallbackTokenTests(unittest.TestCase):
    def test_callback_data_round_trips_and_fits_telegram_limit(self) -> None:
        token = flow_core.new_token()
        data = edit_callback_data(token)
        self.assertLessEqual(len(data.encode("utf-8")), 64)
        self.assertEqual(parse_edit_callback(data), token)

    def test_parse_rejects_foreign_callback_data(self) -> None:
        self.assertIsNone(parse_edit_callback("other:123"))
        self.assertIsNone(parse_edit_callback("edit:"))
        self.assertIsNone(parse_edit_callback(""))


class FeatureHelperTests(unittest.TestCase):
    def test_action_callbacks_round_trip_and_fit_limit(self) -> None:
        token = flow_core.new_token()
        for action in ("edit", "vary", "regen", "mix", "upscale", "download", "up2x", "realup", "skuadd", "mpexport"):
            data = flow_core.action_callback_data(action, token)
            self.assertLessEqual(len(data.encode("utf-8")), 64)
            self.assertEqual(flow_core.parse_action_callback(data), (action, token))
        self.assertIsNone(flow_core.parse_action_callback("unknown:tok"))
        self.assertIsNone(flow_core.parse_action_callback("vary:"))

    def test_clamp_num_images(self) -> None:
        self.assertEqual(flow_core.clamp_num_images(4), 4)
        self.assertEqual(flow_core.clamp_num_images(0), 1)
        self.assertEqual(flow_core.clamp_num_images(99), flow_core.MAX_NUM_IMAGES)
        self.assertEqual(flow_core.clamp_num_images("3"), 3)
        self.assertEqual(flow_core.clamp_num_images("abc"), flow_core.DEFAULT_NUM_IMAGES)

    def test_square_aspect_code(self) -> None:
        self.assertEqual(flow_core.aspect_code("square"), "IMAGE_ASPECT_RATIO_SQUARE")
        self.assertEqual(flow_core.aspect_code("1:1"), "IMAGE_ASPECT_RATIO_SQUARE")

    def test_build_ingredients_inputs_multi(self) -> None:
        sources = [
            {"mediaId": "id-1"},
            {"mediaId": "id-2"},
            {"mediaId": "id-3"},
        ]
        out = flow_core.build_ingredients_inputs(sources, max_inputs=2)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["name"], "id-1")
        self.assertEqual(out[1]["name"], "id-2")
        self.assertTrue(all(e["imageInputType"] == "IMAGE_INPUT_TYPE_BASE_IMAGE" for e in out))


class DownloadUrlTests(unittest.TestCase):
    def test_prefers_public_fife_cdn_url(self) -> None:
        img = {
            "mediaStoreUri": "media-store:projects/p/media/abc",
            "fifeUrl": "https://flow-content.google/image/abc?Signature=x",
        }
        self.assertEqual(flow_core.download_url(img), img["fifeUrl"])

    def test_falls_back_to_any_http_url(self) -> None:
        self.assertEqual(
            flow_core.download_url({"uri": "https://cdn/x.png"}), "https://cdn/x.png"
        )

    def test_none_when_no_url(self) -> None:
        self.assertIsNone(flow_core.download_url({"mediaId": "abc"}))
        self.assertIsNone(flow_core.download_url({}))


class MediaIdExtractionTests(unittest.TestCase):
    UID = "56999c74-e7d4-4395-b462-78568cb547e1"

    def test_finds_media_id_by_key(self) -> None:
        body = {"result": {"media": [{"image": {"mediaId": self.UID, "fifeUrl": "http://x"}}]}}
        src = flow_core.media_source_from_response(body)
        self.assertEqual(src["mediaId"], self.UID)

    def test_finds_media_id_under_alternate_key_name(self) -> None:
        # Upload responses may name the field differently than generation does.
        for key in ("id", "name", "media_id"):
            with self.subTest(key=key):
                src = flow_core.media_source_from_response({"asset": {key: self.UID}})
                self.assertIsNotNone(src, key)
                self.assertEqual(src["mediaId"], self.UID)

    def test_derives_id_from_image_url_when_no_id_key(self) -> None:
        # The structural invariant: the id is the UUID in the content URL.
        body = {"x": {"url": f"https://flow-content.google/image/{self.UID}?Signature=z"}}
        src = flow_core.media_source_from_response(body)
        self.assertEqual(src["mediaId"], self.UID)
        self.assertTrue(src["fifeUrl"].startswith("https://"))

    def test_skips_project_and_session_ids(self) -> None:
        other = "11111111-2222-3333-4444-555555555555"
        body = {
            "projectId": other,
            "sessionId": other,
            "workflowId": other,
            "media": {"name": self.UID},
        }
        self.assertEqual(flow_core.extract_media_id(body), self.UID)

    def test_loads_xssi_prefix(self) -> None:
        raw = ")]}'\n" + json.dumps({"media": {"mediaId": self.UID}})
        body = flow_core.loads_xssi(raw)
        self.assertEqual(flow_core.extract_media_id(body), self.UID)

    def test_loads_xssi_returns_none_for_non_json(self) -> None:
        self.assertIsNone(flow_core.loads_xssi("not json at all"))
        self.assertIsNone(flow_core.loads_xssi(""))

    def test_id_from_media_url_variants(self) -> None:
        self.assertEqual(
            flow_core.id_from_media_url(f"https://h/image/{self.UID}?x=1"), self.UID
        )
        self.assertEqual(
            flow_core.id_from_media_url(f"https://h/media/{self.UID}"), self.UID
        )
        self.assertIsNone(flow_core.id_from_media_url("https://h/other/path"))
        self.assertIsNone(flow_core.id_from_media_url(None))

    def test_returns_none_without_any_media_signal(self) -> None:
        self.assertIsNone(flow_core.media_source_from_response({"a": {"b": [1, 2, "x"]}}))
        self.assertIsNone(flow_core.media_source_from_response([]))

    def test_upload_image_payload_matches_capture_contract(self) -> None:
        payload = build_upload_image_payload(
            project_id="proj-1",
            image_bytes="abc123",
            mime_type="image/png",
            file_name="image.png",
        )
        self.assertEqual(IMAGE_UPLOAD_ENDPOINT, "https://aisandbox-pa.googleapis.com/v1/flow/uploadImage")
        self.assertEqual(payload["clientContext"], {"projectId": "proj-1", "tool": "PINHOLE"})
        self.assertEqual(payload["imageBytes"], "abc123")
        # Live capture 2026-06-20: ONLY clientContext + imageBytes. The old extra
        # fields are no longer sent (they made the upload fail).
        self.assertEqual(set(payload.keys()), {"clientContext", "imageBytes"})
        for stale in ("isUserUploaded", "isHidden", "mimeType", "fileName"):
            self.assertNotIn(stale, payload)
        self.assertNotIn("recaptchaContext", json.dumps(payload))

    def test_parse_upload_image_response_preserves_project_and_workflow(self) -> None:
        workflow_id = "11111111-2222-3333-4444-555555555555"
        data = {
            "media": {
                "name": self.UID,
                "projectId": "proj-1",
                "workflowId": workflow_id,
            }
        }
        src = parse_upload_image_response(data)
        self.assertEqual(src["mediaId"], self.UID)
        self.assertEqual(src["_project_id"], "proj-1")
        self.assertEqual(src["workflowId"], workflow_id)


class ImageRegistryTests(unittest.TestCase):
    def test_add_get_round_trip(self) -> None:
        reg = ImageRegistry()
        ref = ImageRef(user_id=7, project_id="p", source={"mediaStoreUri": "m"})
        token = reg.add(ref)
        self.assertIs(reg.get(token), ref)

    def test_registry_is_bounded_and_evicts_oldest(self) -> None:
        reg = ImageRegistry(max_entries=2)
        t1 = reg.add(ImageRef(user_id=1, project_id="p"))
        t2 = reg.add(ImageRef(user_id=2, project_id="p"))
        t3 = reg.add(ImageRef(user_id=3, project_id="p"))
        self.assertEqual(len(reg), 2)
        self.assertIsNone(reg.get(t1))  # oldest evicted
        self.assertIsNotNone(reg.get(t2))
        self.assertIsNotNone(reg.get(t3))


class UserProjectStoreTests(unittest.TestCase):
    def test_persists_and_reloads_per_user_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "user_projects.json"
            store = UserProjectStore(path)
            self.assertIsNone(store.get(42))
            store.set(42, "proj-42")
            store.set(99, "proj-99")

            reloaded = UserProjectStore(path)
            self.assertEqual(reloaded.get(42), "proj-42")
            self.assertEqual(reloaded.get(99), "proj-99")

    def test_corrupt_file_is_ignored_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "user_projects.json"
            path.write_text("{ not json", encoding="utf-8")
            store = UserProjectStore(path)
            self.assertEqual(store.as_dict(), {})
            store.set(1, "p1")
            self.assertEqual(UserProjectStore(path).get(1), "p1")

    def test_remove_and_empty_values_are_handled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "user_projects.json"
            store = UserProjectStore(path)
            store.set(1, "p1")
            store.set(1, "")  # empty project id is a no-op
            self.assertEqual(store.get(1), "p1")
            store.remove(1)
            self.assertIsNone(store.get(1))


class FlowBotWiringStaticTests(unittest.TestCase):
    """Assertions on flow_bot.py source so we don't need aiogram installed."""

    def setUp(self) -> None:
        self.source = (PROJECT_ROOT / "flow_bot.py").read_text(encoding="utf-8")
        self.kb_source = (
            PROJECT_ROOT / "channels" / "telegram" / "keyboards.py"
        ).read_text(encoding="utf-8")
        self.image_action_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "image_action.py"
        ).read_text(encoding="utf-8")
        # /imgn and other image slash commands moved to their own router
        # (Phase 6).
        self.generation_commands_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "generation_commands.py"
        ).read_text(encoding="utf-8")

    def test_edit_button_and_callback_handler_present(self) -> None:
        # Labels now come from flow_copy; the edit button uses the "edit" action.
        self.assertIn('action_callback_data("edit", token)', self.kb_source)
        # The image-action handler carries a precise filter since Phase 6;
        # unknown callbacks are acked by the tail fallback router instead.
        self.assertIn("@router.callback_query(_is_action_callback)", self.image_action_router_source)
        self.assertNotIn("@dp.callback_query()\n", self.source)
        self.assertIn("async def on_image_action", self.image_action_router_source)
        self.assertIn("parse_action_callback", self.image_action_router_source)
        self.assertIn("async def _edit_and_send", self.source)

    def test_all_five_features_wired(self) -> None:
        # 1) ingredients/mix, 2) square aspect, 3) original download, 4) count, 5) vary.
        for needle in (
            "build_ingredients_inputs",          # #1 ingredients
            "async def _send_original_file",     # #3 full-quality download
            "answer_document",                   # #3 document (no Telegram compression)
            "download_url",                      # #3 best fetchable URL
            "clamp_num_images",                  # #4 flexible count
            "async def _vary_and_send",          # #5 variations
            "async def _regen_and_send",         # "ещё" regen
            "mix_baskets",                       # mix basket state
        ):
            self.assertIn(needle, self.source, needle)
        # /square and /imgn commands moved to the generation-commands router
        # (Phase 6).
        self.assertIn('aspect_ratio="square"', self.generation_commands_router_source)  # #2 square
        self.assertIn('Command("imgn")', self.generation_commands_router_source)

    def test_callback_actions_use_ref_user_not_bot(self) -> None:
        # In callbacks message.from_user is the bot; new images must be owned by
        # the real user, so helpers derive the actor from ref.user_id.
        self.assertIn("user_id = ref.user_id", self.source)
        self.assertIn("actor_id=ref.user_id", self.source)
        self.assertIn("actor_id: int | None = None", self.source)
        self.assertIn("user_id = actor_id if actor_id is not None else message.from_user.id", self.source)

    def test_result_regen_button_generates_one_image(self) -> None:
        # The result button is labeled with the default one-image regen price,
        # so the handler must request one image and charge one unit.
        start = self.source.index("async def _regen_and_send")
        end = self.source.index("async def _send_original_file", start)
        block = self.source[start:end]
        self.assertIn("num_images=1", block)
        self.assertIn('action="regen"', block)

    def test_rate_limit_waits_instead_of_rejecting(self) -> None:
        # Early cooldown should sleep the remainder and proceed, not reject.
        self.assertIn("MAX_AUTO_WAIT_SEC", self.source)
        self.assertIn("await asyncio.sleep(remaining)", self.source)
        self.assertIn("async def user_slot", self.source)
        # Anti-abuse: parallel request while one is in-flight is rejected.
        self.assertIn("user_busy", self.source)
        self.assertIn("class RateLimited", self.source)
        self.assertEqual(self.source.count("COOLDOWN_SEC = 10"), 1)

    def test_photo_upload_and_edit_wired(self) -> None:
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        self.assertIn("@dp.message(F.photo)", self.source)
        self.assertIn("async def handle_photo", self.source)
        # Загрузка идёт через keeper аккаунта юзера (multi-account роутинг).
        self.assertIn("_keeper_for_acc(acc_id).upload_image", self.source)
        self.assertIn("_account_for_image(user_id, prefer_image_only=True)", self.source)
        self.assertIn("async def upload_image", self.source)
        self.assertIn("set_input_files", self.source)
        self.assertIn("media_source_from_response", self.source)

    def test_upload_is_robust_not_host_or_field_guessed(self) -> None:
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        # Regression: do NOT filter responses to one host or require an exact
        # field; parse XSSI, dedupe vs baseline DOM ids, dump schema on failure.
        self.assertIn("loads_xssi", self.source)
        self.assertIn("baseline_ids", self.source)
        self.assertIn("_page_media_ids", self.source)
        self.assertIn("UPLOAD_CAPTURE_FILE", self.source)
        # The upload interceptor must not be scoped to a single host.
        start = self.source.index("async def _on_upload_response")
        end = self.source.index("async def _page_media_ids")
        upload_block = self.source[start:end]
        self.assertNotIn("aisandbox-pa.googleapis.com", upload_block)

    def test_uploaded_photo_edits_in_upload_project(self) -> None:
        # Edit must target the project the upload actually landed in.
        self.assertIn('source.pop("_project_id", None)', self.source)
        self.assertIn("upload_project", self.source)
        self.assertIn("async def _upload_image_ref_from_file_id", self.source)
        self.assertIn('source.setdefault("_tg_file_id", file_id)', self.source)

    def test_pending_edit_routing_in_plain_text_handler(self) -> None:
        self.assertIn("pending_edits", self.source)
        self.assertIn("token = pending_edits.get(user_id)", self.source)
        self.assertIn("ok = await _edit_and_send", self.source)
        self.assertIn("if ok:", self.source)
        self.assertIn("pending_edits.pop(user_id, None)", self.source)

    def test_edit_rate_limit_keeps_context_copy(self) -> None:
        self.assertIn("def _is_rate_limit_error", self.source)
        self.assertIn("image_edit_rate_limited", self.source)
        # image_edit_failover msg key exists in flow_copy but is shown only in logs, not to users
        self.assertIn("_reupload_ref_for_edit_failover", self.source)
        self.assertIn("async def _reupload_ref_for_edit_failover", self.source)
        self.assertIn("exclude={current_account_id}", self.source)
        self.assertIn("_mark_image_account_failure(ref.account_id, result)", self.source)
        self.assertIn("_generate_for(failover_ref, failover_inputs)", self.source)
        self.assertIn("1–3 минуты", flow_copy.msg("image_edit_rate_limited"))

    def test_no_fallback_image_403_classified_as_rate_limited(self) -> None:
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        start = self.source.index("async def generate_images")
        end = self.source.index("async def run_captured_request", start)
        block = self.source[start:end]
        self.assertIn("saw_403 = False", block)
        self.assertIn("saw_unusual_activity = False", block)
        self.assertIn("saw_403 = True", block)
        self.assertIn("PUBLIC_ERROR_UNUSUAL_ACTIVITY", block)
        self.assertIn('"account_risk": "unusual_activity"', block)
        no_fallback = block.index("if saw_403:")
        rate_limited_return = block.index(
            'return {"error": flow_copy.msg("rate_limited")}',
            no_fallback,
        )
        gen_failed_return = block.index(
            'return {"error": flow_copy.msg("gen_failed")}',
            no_fallback,
        )
        self.assertLess(rate_limited_return, gen_failed_return)

    def test_unusual_activity_cools_down_image_account(self) -> None:
        self.assertIn("def _mark_image_account_failure", self.source)
        helper = self.source[
            self.source.index("def _mark_image_account_failure"):
            self.source.index("async def _download_ref_image_bytes")
        ]
        self.assertIn('(result or {}).get("account_risk") == "unusual_activity"', helper)
        self.assertIn("account_pool.mark_cooldown(account_id)", helper)
        self.assertIn("account_pool.mark_failure(account_id)", helper)
        # Провайдерский 429 → аккаунт сразу в кулдаун (общий cooldown пула
        # блокирует и картинки, и видео на нём).
        self.assertIn("_is_rate_limit_error(result)", helper)
        self.assertIn('"reason": "rate_limited", "op": "image"', helper)

        edit = self.source[
            self.source.index("async def _do_edit_and_send"):
            self.source.index("async def _run_i2i")
        ]
        self.assertIn("_mark_image_account_failure(ref.account_id, result)", edit)
        self.assertIn("_mark_image_account_failure(failover_ref.account_id, result)", edit)

    def test_per_user_project_creation_wired(self) -> None:
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        self.assertIn("async def ensure_user_project", self.source)
        self.assertIn("async def create_new_project", self.source)
        self.assertIn("project_store = UserProjectStore", self.source)
        self.assertIn("project_id=project_id", self.source)

    def test_edit_disables_browser_fallback(self) -> None:
        self.assertIn("allow_browser_fallback=False", self.source)

    def test_callback_enforces_owner_match(self) -> None:
        self.assertIn("ref.user_id != user_id", self.source)

    def test_project_creation_uses_separate_tab_not_main_page(self) -> None:
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        # Регрессия: создание проекта НЕ должно делать goto на главной странице
        # (self._page) — иначе ломается живая сессия и поле ввода.
        self.assertIn("tab = await self._context.new_page()", self.source)
        self.assertIn("await tab.goto(FLOW_URL", self.source)
        # Главная страница уходит на FLOW_URL только один раз — при старте
        # сессии (_start_locked). Создание проекта работает в отдельной вкладке.
        self.assertEqual(self.source.count("self._page.goto(FLOW_URL"), 1)

    def test_project_creation_has_failure_backoff(self) -> None:
        self.assertIn("_per_user_projects_enabled", self.source)
        self.assertIn("PROJECT_CREATION_MAX_FAILURES", self.source)
        self.assertIn("PER_USER_PROJECTS", self.source)

    def test_edit_shape_is_captured_not_guessed(self) -> None:
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        # Edit must replay a captured real request, never a hardcoded guess.
        self.assertIn("def _maybe_capture_edit", self.source)
        self.assertIn("def note_image", self.source)
        self.assertIn("_keeper_for_acc(account_id).note_image(img)", self.source)
        self.assertIn("capture = load_edit_capture(EDIT_CAPTURE_FILE)", self.source)
        self.assertIn("build_image_inputs(ref.source, capture)", self.source)

    def test_edit_capture_is_guarded_against_breaking_requests(self) -> None:
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        # The interceptor must swallow its own errors so generation never breaks.
        idx = self.source.index("def _maybe_capture_edit")
        end = self.source.index("# ── обновление Bearer", idx)
        body = self.source[idx:end]
        self.assertIn("except Exception:", body)


if __name__ == "__main__":
    unittest.main()
