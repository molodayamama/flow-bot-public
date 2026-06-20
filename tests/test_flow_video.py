"""Offline unit tests for video-generation helpers in flow_core.py.

These tests require no network, no browser, no secrets.
Run with: python -m unittest discover -s tests -p "test_flow_video.py"
"""
import unittest
from flow_core import (
    VIDEO_MODEL_KEYS,
    VIDEO_ASPECT_MAP,
    VIDEO_ENDPOINT,
    VIDEO_EDIT_ENDPOINT,
    VIDEO_EXTEND_ENDPOINT,
    VIDEO_POLL_ENDPOINT,
    VIDEO_STATUS_SCHEDULED,
    VIDEO_STATUS_ACTIVE,
    VIDEO_STATUS_SUCCESSFUL,
    VIDEO_STATUS_FAILED,
    VIDEO_TERMINAL_STATUSES,
    VideoRef,
    video_model_key,
    video_aspect_code,
    video_media_redirect_url,
    build_video_edit_payload,
    build_video_extend_payload,
    build_video_frame_images,
    build_video_payload,
    build_video_poll_payload,
    build_video_reference_images,
    flow_scene_create_url,
    flow_scene_workflows_url,
    parse_video_gen_response,
    parse_video_scene_id,
    check_video_poll_status,
    parse_video_batch_id,
    parse_video_result,
    VIDEO_CONCAT_ENDPOINT,
    VIDEO_CONCAT_STATUS_ENDPOINT,
    parse_scene_segments,
    build_concat_payload,
    parse_concat_operation_name,
    build_concat_status_payload,
    parse_concat_status,
    upload_video_ids_from_response,
    VIDEO_UPLOAD_START_URL,
    VIDEO_UPLOAD_PUT_URL,
    video_edit_end_frame,
    video_duration_from_poll_item,
    CREDITS_ENDPOINT,
    FLOW_BROWSER_API_KEY,
    parse_credits_response,
)

FAKE_BATCH_ID = "619ce05f-5416-4ca4-8840-0a74a1f669f2"
# Real values from tools/capture_video.py --no-abort capture
REAL_MEDIA_ID   = "3a1ebe94-c8bb-4727-8565-9135b0f1fff7"
REAL_PROJECT_ID = "7626e48a-5faf-4e6c-a26c-94a3cf02191c"
REAL_WORKFLOW_ID = "770a887f-b5f6-40ec-841a-2810818c2259"
REAL_SCENE_ID = "c2fd6ac1-7e3d-4752-81a6-740e0a431732"


class TestVideoRef(unittest.TestCase):
    def test_source_media_id_is_optional_extend_metadata(self):
        ref = VideoRef(user_id=1, project_id=REAL_PROJECT_ID, media_id=REAL_MEDIA_ID)
        self.assertIsNone(ref.source_media_id)

        extended = VideoRef(
            user_id=1,
            project_id=REAL_PROJECT_ID,
            media_id="extension-media-id",
            source_media_id=REAL_MEDIA_ID,
            mode="extend",
        )
        self.assertEqual(extended.source_media_id, REAL_MEDIA_ID)
        self.assertEqual(extended.mode, "extend")


class TestVideoModelKey(unittest.TestCase):
    def test_confirmed_omni_flash_4s(self):
        self.assertEqual(video_model_key("omni-flash-4s"), "abra_t2v_4s")

    def test_confirmed_veo_lite(self):
        # Confirmed from real --no-abort capture
        self.assertEqual(video_model_key("veo-lite"), "veo_3_1_t2v_lite")

    def test_all_omni_flash_durations_have_keys(self):
        for dur in (4, 6, 8, 10):
            key = f"omni-flash-{dur}s"
            self.assertIn(key, VIDEO_MODEL_KEYS, f"missing key for {key}")
            self.assertTrue(VIDEO_MODEL_KEYS[key])

    def test_unknown_falls_back_to_omni_flash_4s(self):
        self.assertEqual(video_model_key("nonsense"), VIDEO_MODEL_KEYS["omni-flash-4s"])

    def test_case_insensitive(self):
        self.assertEqual(video_model_key("OMNI-FLASH-4S"), video_model_key("omni-flash-4s"))


class TestVideoAspectCode(unittest.TestCase):
    def test_portrait_confirmed(self):
        # Confirmed from real captured payload
        self.assertEqual(video_aspect_code("portrait"), "VIDEO_ASPECT_RATIO_PORTRAIT")

    def test_aliases(self):
        self.assertEqual(video_aspect_code("9:16"), video_aspect_code("portrait"))
        self.assertEqual(video_aspect_code("16:9"), video_aspect_code("landscape"))

    def test_unknown_falls_back_to_landscape(self):
        self.assertEqual(video_aspect_code("weird"), VIDEO_ASPECT_MAP["landscape"])


class TestBuildVideoPayload(unittest.TestCase):
    def _build(self, **kwargs):
        defaults = dict(
            prompt="яблочко бежит по полю",
            project_id="22834ff8-8ce1-494a-a022-3b6b973f662f",
            captcha_token="tok",
            aspect="portrait",
            model_key="omni-flash-4s",
            session_id=";1780869111948",
            batch_id=FAKE_BATCH_ID,
        )
        defaults.update(kwargs)
        return build_video_payload(**defaults)

    def test_top_level_structure(self):
        p = self._build()
        self.assertIn("mediaGenerationContext", p)
        self.assertIn("clientContext", p)
        self.assertIn("requests", p)
        self.assertTrue(p.get("useV2ModelConfig"))

    def test_media_generation_context(self):
        p = self._build()
        mgc = p["mediaGenerationContext"]
        self.assertEqual(mgc["batchId"], FAKE_BATCH_ID)
        self.assertEqual(mgc["audioFailurePreference"], "BLOCK_SILENCED_VIDEOS")

    def test_client_context(self):
        p = self._build()
        ctx = p["clientContext"]
        self.assertEqual(ctx["tool"], "PINHOLE")
        self.assertEqual(ctx["userPaygateTier"], "PAYGATE_TIER_ONE")
        self.assertEqual(ctx["recaptchaContext"]["token"], "tok")
        self.assertEqual(
            ctx["recaptchaContext"]["applicationType"],
            "RECAPTCHA_APPLICATION_TYPE_WEB",
        )

    def test_request_shape(self):
        p = self._build()
        self.assertEqual(len(p["requests"]), 1)
        req = p["requests"][0]
        # Confirmed field names from real capture
        self.assertIn("aspectRatio", req)
        self.assertIn("textInput", req)
        self.assertIn("videoModelKey", req)
        self.assertIn("seed", req)
        self.assertIn("metadata", req)

    def test_prompt_in_structured_parts(self):
        p = self._build(prompt="a cat runs")
        parts = p["requests"][0]["textInput"]["structuredPrompt"]["parts"]
        self.assertEqual(parts[0]["text"], "a cat runs")

    def test_model_key_omni_flash_4s(self):
        p = self._build(model_key="omni-flash-4s")
        self.assertEqual(p["requests"][0]["videoModelKey"], "abra_t2v_4s")

    def test_aspect_portrait(self):
        p = self._build(aspect="portrait")
        self.assertEqual(p["requests"][0]["aspectRatio"], "VIDEO_ASPECT_RATIO_PORTRAIT")

    def test_seed_in_valid_range(self):
        p = self._build()
        seed = p["requests"][0]["seed"]
        self.assertGreaterEqual(seed, 1000)
        self.assertLess(seed, 10000)

    def test_matches_real_capture_shape(self):
        """Verify payload structure matches the real captured request exactly."""
        p = self._build()
        req = p["requests"][0]
        # These fields were present in the real capture
        self.assertIsInstance(req["metadata"], dict)
        self.assertIsInstance(req["textInput"]["structuredPrompt"]["parts"], list)
        self.assertIsInstance(req["aspectRatio"], str)
        self.assertIsInstance(req["videoModelKey"], str)
        self.assertIsInstance(req["seed"], int)

    def test_reference_images_for_ingredients(self):
        # Confirmed from the live --ingredients capture: refs carry mediaId +
        # imageUsageType, fe_id_ prefix stripped.
        refs = build_video_reference_images([
            {"mediaId": "fe_id_short-1"},
            {"mediaId": "short-2"},
        ])
        self.assertEqual(refs, [
            {"mediaId": "short-1", "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"},
            {"mediaId": "short-2", "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"},
        ])
        # Ingredients payload serializes referenceImages and uses the r2v key.
        # Live capture 2026-06-20: Veo r2v key is veo_3_1_r2v_lite (NO orientation
        # suffix). Orientation is carried by aspectRatio.
        p = self._build(model_key="veo-lite", aspect="portrait", reference_images=refs)
        req = p["requests"][0]
        self.assertEqual(req["referenceImages"], refs)
        self.assertEqual(req["videoModelKey"], "veo_3_1_r2v_lite")

    def test_reference_model_key_is_family_aware(self):
        import flow_core
        # Live captures 2026-06-20: Veo -> veo_3_1_r2v_<tier>; Omni Flash -> abra_r2v_<dur>s.
        self.assertEqual(flow_core.video_reference_model_key("veo-lite", "portrait"), "veo_3_1_r2v_lite")
        self.assertEqual(flow_core.video_reference_model_key("veo-fast", "landscape"), "veo_3_1_r2v_fast")
        self.assertEqual(flow_core.video_reference_model_key("omni-flash-6s", "16:9"), "abra_r2v_6s")
        self.assertEqual(flow_core.video_reference_model_key("omni-flash-4s", "portrait"), "abra_r2v_4s")

    def test_frames_model_key_tiers(self):
        import flow_core
        self.assertEqual(flow_core.video_frames_model_key("veo-lite"), "veo_3_1_interpolation_lite")
        self.assertEqual(flow_core.video_frames_model_key("veo-fast"), "veo_3_1_interpolation_fast")
        self.assertEqual(flow_core.video_frames_model_key("veo-quality"), "veo_3_1_interpolation_quality")

    # Full-frame crop the bot sends when the user did no cropping (matches the
    # live batchAsyncGenerateVideoStartAndEndImage request shape).
    FULL_CROP = {"top": 0, "left": 0, "bottom": 1, "right": 1}

    def test_start_end_images_for_frames(self):
        # Frames endpoint references uploaded assets by their mediaId and always
        # carries cropCoordinates (default: the full frame).
        start, end = build_video_frame_images(
            {"mediaId": "start-mg"},
            {"mediaId": "end-mg"},
        )
        self.assertEqual(start, {"mediaId": "start-mg", "cropCoordinates": self.FULL_CROP})
        self.assertEqual(end, {"mediaId": "end-mg", "cropCoordinates": self.FULL_CROP})
        # With both frames set, the payload serializes startImage/endImage and
        # switches to the interpolation model key (confirmed from the live
        # --frames capture: veo-lite -> veo_3_1_interpolation_lite).
        p = self._build(model_key="veo-lite", start_image=start, end_image=end)
        req = p["requests"][0]
        self.assertEqual(req["startImage"], start)
        self.assertEqual(req["endImage"], end)
        self.assertEqual(req["videoModelKey"], "veo_3_1_interpolation_lite")

    def test_frame_media_id_strips_fe_id_prefix(self):
        # The captured frame imageId carries an "fe_id_" prefix that the
        # endpoint does not expect on mediaId.
        start, end = build_video_frame_images(
            {"mediaId": "fe_id_3b623a32"},
            {"mediaId": "fe_id_f8479871"},
        )
        self.assertEqual(start["mediaId"], "3b623a32")
        self.assertEqual(end["mediaId"], "f8479871")

    def test_frame_crop_passthrough(self):
        # A source carrying its own cropCoordinates overrides the full-frame default.
        crop = {"top": 0.2, "left": 0.0, "bottom": 0.8, "right": 1.0}
        start, _ = build_video_frame_images({"mediaId": "m", "cropCoordinates": crop})
        self.assertEqual(start["cropCoordinates"], crop)


class TestNativeVideoEditPayload(unittest.TestCase):
    def test_endpoint_verified_from_capture(self):
        self.assertIn("aisandbox-pa.googleapis.com", VIDEO_EDIT_ENDPOINT)
        self.assertIn("batchAsyncGenerateVideoEditVideo", VIDEO_EDIT_ENDPOINT)

    def test_payload_shape_matches_capture(self):
        p = build_video_edit_payload(
            prompt="make the camera orbit",
            project_id=REAL_PROJECT_ID,
            captcha_token="tok",
            aspect="landscape",
            session_id=";1780931934213",
            batch_id=FAKE_BATCH_ID,
            source_media_id=REAL_MEDIA_ID,
            source_workflow_id=REAL_WORKFLOW_ID,
        )
        req = p["requests"][0]
        self.assertEqual(req["videoModelKey"], "abra_edit")
        self.assertEqual(req["metadata"], {"workflowId": REAL_WORKFLOW_ID})
        self.assertEqual(req["videoInput"]["mediaId"], REAL_MEDIA_ID)
        self.assertEqual(req["videoInput"]["startFrameIndex"], 0)
        self.assertEqual(req["videoInput"]["endFrameIndex"], 240)
        self.assertNotIn("useV2ModelConfig", p)


class TestNativeVideoExtendPayload(unittest.TestCase):
    def test_endpoint_verified_from_capture(self):
        self.assertIn("aisandbox-pa.googleapis.com", VIDEO_EXTEND_ENDPOINT)
        self.assertIn("batchAsyncGenerateVideoExtendVideo", VIDEO_EXTEND_ENDPOINT)

    def test_payload_shape_matches_capture(self):
        p = build_video_extend_payload(
            prompt="continue into a sunrise",
            project_id=REAL_PROJECT_ID,
            captcha_token="tok",
            aspect="landscape",
            model_key="veo-lite",
            session_id=";1780932112774",
            batch_id=FAKE_BATCH_ID,
            source_media_id=REAL_MEDIA_ID,
            scene_id=REAL_SCENE_ID,
        )
        req = p["requests"][0]
        self.assertEqual(req["videoModelKey"], "veo_3_1_extension_lite")
        self.assertEqual(req["metadata"], {"sceneId": REAL_SCENE_ID})
        self.assertEqual(req["videoInput"], {"mediaId": REAL_MEDIA_ID})
        self.assertEqual(
            p["mediaGenerationContext"]["sceneContext"],
            {"sceneId": REAL_SCENE_ID, "position": 1},
        )
        self.assertTrue(p["useV2ModelConfig"])


class TestVideoSceneHelpers(unittest.TestCase):
    def test_scene_urls_match_captured_routes(self):
        create = flow_scene_create_url(REAL_PROJECT_ID)
        workflows = flow_scene_workflows_url(REAL_SCENE_ID, REAL_PROJECT_ID)
        self.assertIn(f"/v1/flow/projects/{REAL_PROJECT_ID}/scenes", create)
        self.assertIn(f"/v1/flow/scene/{REAL_SCENE_ID}/workflows", workflows)
        self.assertIn("sceneId=", workflows)
        self.assertIn("projectId=", workflows)

    def test_parse_scene_id_from_create_response(self):
        data = {"scene": {"sceneId": REAL_SCENE_ID}, "sceneWorkflows": []}
        self.assertEqual(parse_video_scene_id(data), REAL_SCENE_ID)

    def test_parse_scene_id_from_workflows_response(self):
        data = {"sceneWorkflows": [{"sceneId": REAL_SCENE_ID}]}
        self.assertEqual(parse_video_scene_id(data), REAL_SCENE_ID)

    def test_parse_scene_id_missing(self):
        self.assertIsNone(parse_video_scene_id({"sceneWorkflows": [{}]}))


class TestParseVideoBatchId(unittest.TestCase):
    def test_top_level_batch_id(self):
        self.assertEqual(parse_video_batch_id({"batchId": "abc-123"}), "abc-123")

    def test_operation_name(self):
        self.assertEqual(
            parse_video_batch_id({"operation": {"name": "ops/xyz"}}),
            "ops/xyz",
        )

    def test_empty_returns_none(self):
        self.assertIsNone(parse_video_batch_id({}))


class TestParseVideoResult(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(parse_video_result({}), [])

    def test_responses_uri(self):
        data = {"responses": [{"generatedVideo": {"uri": "https://cdn.example.com/v.mp4"}}]}
        self.assertEqual(parse_video_result(data), ["https://cdn.example.com/v.mp4"])

    def test_media_variant(self):
        data = {
            "media": [
                {"video": {"generatedVideo": {"fifeUrl": "https://a.com/v.mp4"}}}
            ]
        }
        self.assertEqual(parse_video_result(data), ["https://a.com/v.mp4"])

    def test_unknown_format_empty(self):
        self.assertEqual(parse_video_result({"other": []}), [])


class TestVideoEndpoint(unittest.TestCase):
    def test_endpoint_verified(self):
        self.assertIn("aisandbox-pa.googleapis.com", VIDEO_ENDPOINT)
        self.assertIn("batchAsyncGenerateVideoText", VIDEO_ENDPOINT)

    def test_poll_endpoint_verified(self):
        self.assertIn("aisandbox-pa.googleapis.com", VIDEO_POLL_ENDPOINT)
        self.assertIn("batchCheckAsyncVideoGenerationStatus", VIDEO_POLL_ENDPOINT)


class TestVideoMediaRedirectUrl(unittest.TestCase):
    def test_real_pattern(self):
        # Confirmed from the page <video>.src
        url = video_media_redirect_url(REAL_MEDIA_ID)
        self.assertEqual(
            url,
            f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={REAL_MEDIA_ID}",
        )

    def test_host_is_labs_google_not_api(self):
        url = video_media_redirect_url(REAL_MEDIA_ID)
        self.assertIn("labs.google", url)
        self.assertNotIn("aisandbox-pa.googleapis.com", url)

    def test_special_chars_are_escaped(self):
        url = video_media_redirect_url("a/b c")
        self.assertNotIn(" ", url)
        self.assertNotIn("a/b c", url)


class TestBuildVideoPollPayload(unittest.TestCase):
    def test_shape_matches_real_capture(self):
        # Real capture: {"media": [{"name": media_id, "projectId": project_id}]}
        p = build_video_poll_payload(REAL_MEDIA_ID, REAL_PROJECT_ID)
        self.assertEqual(p, {
            "media": [{"name": REAL_MEDIA_ID, "projectId": REAL_PROJECT_ID}]
        })


class TestParseVideoGenResponse(unittest.TestCase):
    def test_real_capture_response(self):
        # Mirrors the real batchAsyncGenerateVideoText response shape
        data = {
            "remainingCredits": 1961,
            "media": [
                {"name": REAL_MEDIA_ID, "projectId": REAL_PROJECT_ID,
                 "workflowId": REAL_WORKFLOW_ID}
            ],
        }
        info = parse_video_gen_response(data)
        self.assertEqual(
            info,
            {
                "media_id": REAL_MEDIA_ID,
                "project_id": REAL_PROJECT_ID,
                "workflow_id": REAL_WORKFLOW_ID,
            },
        )

    def test_scene_id_is_preserved_when_present(self):
        data = {
            "media": [
                {
                    "name": REAL_MEDIA_ID,
                    "projectId": REAL_PROJECT_ID,
                    "workflowId": REAL_WORKFLOW_ID,
                    "sceneId": REAL_SCENE_ID,
                }
            ],
        }
        self.assertEqual(
            parse_video_gen_response(data),
            {
                "media_id": REAL_MEDIA_ID,
                "project_id": REAL_PROJECT_ID,
                "workflow_id": REAL_WORKFLOW_ID,
                "scene_id": REAL_SCENE_ID,
            },
        )

    def test_missing_media_returns_none(self):
        self.assertIsNone(parse_video_gen_response({}))
        self.assertIsNone(parse_video_gen_response({"media": []}))

    def test_partial_media_returns_none(self):
        # Has name but no projectId
        self.assertIsNone(parse_video_gen_response({"media": [{"name": REAL_MEDIA_ID}]}))


class TestCheckVideoPollStatus(unittest.TestCase):
    def _poll_response(self, status):
        # Mirrors the real polling response nesting
        return {
            "media": [
                {
                    "name": REAL_MEDIA_ID,
                    "mediaMetadata": {
                        "mediaStatus": {"mediaGenerationStatus": status}
                    },
                }
            ]
        }

    def test_scheduled(self):
        status, item = check_video_poll_status(self._poll_response(VIDEO_STATUS_SCHEDULED))
        self.assertEqual(status, VIDEO_STATUS_SCHEDULED)
        self.assertIsNotNone(item)

    def test_active(self):
        status, _ = check_video_poll_status(self._poll_response(VIDEO_STATUS_ACTIVE))
        self.assertEqual(status, VIDEO_STATUS_ACTIVE)

    def test_successful(self):
        status, _ = check_video_poll_status(self._poll_response(VIDEO_STATUS_SUCCESSFUL))
        self.assertEqual(status, VIDEO_STATUS_SUCCESSFUL)
        self.assertIn(status, VIDEO_TERMINAL_STATUSES)

    def test_failed_is_terminal(self):
        self.assertIn(VIDEO_STATUS_FAILED, VIDEO_TERMINAL_STATUSES)

    def test_empty_response(self):
        status, item = check_video_poll_status({})
        self.assertEqual(status, "")
        self.assertIsNone(item)

    def test_status_progression_is_terminal_only_at_end(self):
        self.assertNotIn(VIDEO_STATUS_SCHEDULED, VIDEO_TERMINAL_STATUSES)
        self.assertNotIn(VIDEO_STATUS_ACTIVE, VIDEO_TERMINAL_STATUSES)


class TestVideoConcatenation(unittest.TestCase):
    """Verified against a real capture of Flow's 'download full' action."""

    # Real scene-workflows payload (unordered, mixed/missing positions).
    SCENE = {"sceneWorkflows": [
        {"workflow": {"metadata": {"primaryMediaId": "a291413d-c10b-4820-acd6-66c14e5eb7e1"}},
         "sceneWorkflowMetadata": {"totalDuration": "4s", "startTime": "0s", "endTime": "3.500s"}},
        {"workflow": {"metadata": {"primaryMediaId": "e4762c8f-6582-4ed5-987d-a99836e8b67a"}},
         "sceneWorkflowMetadata": {"position": 2, "totalDuration": "8s", "startTime": "0s", "endTime": "8s"}},
        {"workflow": {"metadata": {"primaryMediaId": "758cc5aa-f381-4900-a3bb-3def6264a557"}},
         "sceneWorkflowMetadata": {"position": 3, "totalDuration": "8s", "startTime": "0s", "endTime": "8s"}},
        {"workflow": {"metadata": {"primaryMediaId": "0d21b79c-2762-453c-aac0-f82dd642b1ea"}},
         "sceneWorkflowMetadata": {"position": 1, "totalDuration": "8s", "startTime": "0s", "endTime": "8s"}},
    ]}

    def test_endpoints_match_captured_routes(self):
        self.assertEqual(VIDEO_CONCAT_ENDPOINT,
                         "https://aisandbox-pa.googleapis.com/v1:runVideoFxConcatenation")
        self.assertEqual(VIDEO_CONCAT_STATUS_ENDPOINT,
                         "https://aisandbox-pa.googleapis.com/v1:runVideoFxCheckConcatenationStatus")

    def test_segments_sorted_by_position_missing_is_zero(self):
        segs = parse_scene_segments(self.SCENE)
        self.assertEqual(
            [s["media_id"][:8] for s in segs],
            ["a291413d", "0d21b79c", "e4762c8f", "758cc5aa"],
        )

    def test_segments_skip_entries_without_media_id(self):
        data = {"sceneWorkflows": [{"workflow": {"metadata": {}}, "sceneWorkflowMetadata": {}}]}
        self.assertEqual(parse_scene_segments(data), [])

    def test_concat_payload_matches_real_request(self):
        payload = build_concat_payload(parse_scene_segments(self.SCENE))
        inputs = payload["inputVideos"]
        # First (trimmed 4s -> 3.5s) segment, byte-exact to the captured request.
        self.assertEqual(inputs[0], {
            "mediaGenerationId": "a291413d-c10b-4820-acd6-66c14e5eb7e1",
            "length": "4000000000",
            "startTimeOffset": "0s",
            "endTimeOffset": "3.5s",
        })
        # 8s segment expressed in nanoseconds.
        self.assertEqual(inputs[1]["length"], "8000000000")
        self.assertEqual(inputs[1]["endTimeOffset"], "8s")
        self.assertEqual(len(inputs), 4)

    def test_parse_operation_name(self):
        resp = {"operation": {"operation": {"name": "projects/365941595420/locations/us-east4/jobs/b54e0835"}}}
        self.assertEqual(parse_concat_operation_name(resp),
                         "projects/365941595420/locations/us-east4/jobs/b54e0835")

    def test_parse_operation_name_missing(self):
        self.assertIsNone(parse_concat_operation_name({}))
        self.assertIsNone(parse_concat_operation_name({"operation": {}}))

    def test_status_payload_roundtrip(self):
        name = "projects/x/locations/us-east4/jobs/abc"
        self.assertEqual(build_concat_status_payload(name),
                         {"operation": {"operation": {"name": name}}})

    def test_parse_status_active_has_no_video(self):
        status, enc = parse_concat_status(
            {"status": "MEDIA_GENERATION_STATUS_ACTIVE", "outputUri": "", "mediaGenerationId": ""})
        self.assertEqual(status, "MEDIA_GENERATION_STATUS_ACTIVE")
        self.assertIsNone(enc)

    def test_parse_status_successful_carries_encoded_video(self):
        status, enc = parse_concat_status(
            {"status": "MEDIA_GENERATION_STATUS_SUCCESSFUL", "inputsCount": 5, "encodedVideo": "QUJD"})
        self.assertEqual(status, "MEDIA_GENERATION_STATUS_SUCCESSFUL")
        self.assertEqual(enc, "QUJD")


class TestUploadVideoIds(unittest.TestCase):
    """Verified against tools/capture_video.py --upload-edit (seq 18 PUT response)."""

    # Exact body the resumable PUT returned for a user-uploaded video.
    REAL_PUT_RESPONSE = {
        "status": "final",
        "mediaServerId": "1e123e08-456e-49ea-9a17-ad8fe288168e",
        "workflowServerId": "af25b4f5-a6a2-4a9d-ad5c-2d9ebd251a08",
        "workflowDisplayName": "",
        "videoWidth": 1280,
        "videoHeight": 720,
    }

    def test_proxy_urls_match_capture(self):
        self.assertEqual(VIDEO_UPLOAD_START_URL, "/fx/api/upload-video?action=start")
        self.assertEqual(VIDEO_UPLOAD_PUT_URL, "/fx/api/upload-video?action=upload")

    def test_extracts_media_and_workflow_from_real_response(self):
        ids = upload_video_ids_from_response(self.REAL_PUT_RESPONSE)
        self.assertIsNotNone(ids)
        self.assertEqual(ids["mediaId"], "1e123e08-456e-49ea-9a17-ad8fe288168e")
        self.assertEqual(ids["workflowId"], "af25b4f5-a6a2-4a9d-ad5c-2d9ebd251a08")
        self.assertEqual(ids["width"], 1280)
        self.assertEqual(ids["height"], 720)

    def test_edit_payload_consumes_uploaded_ids(self):
        # The edit request must carry the uploaded video's mediaId + workflowId.
        ids = upload_video_ids_from_response(self.REAL_PUT_RESPONSE)
        payload = build_video_edit_payload(
            prompt="убери скакалку",
            project_id=REAL_PROJECT_ID,
            captcha_token="tok",
            aspect="landscape",
            session_id=";1",
            batch_id=FAKE_BATCH_ID,
            source_media_id=ids["mediaId"],
            source_workflow_id=ids["workflowId"],
        )
        req = payload["requests"][0]
        self.assertEqual(req["videoModelKey"], "abra_edit")
        self.assertEqual(req["videoInput"]["mediaId"], ids["mediaId"])
        self.assertEqual(req["metadata"]["workflowId"], ids["workflowId"])

    def test_workflow_id_optional(self):
        ids = upload_video_ids_from_response(
            {"status": "final", "mediaServerId": "1e123e08-456e-49ea-9a17-ad8fe288168e"})
        self.assertEqual(ids["mediaId"], "1e123e08-456e-49ea-9a17-ad8fe288168e")
        self.assertNotIn("workflowId", ids)

    def test_none_when_no_media_id(self):
        self.assertIsNone(upload_video_ids_from_response({"status": "active"}))
        self.assertIsNone(upload_video_ids_from_response(None))
        self.assertIsNone(upload_video_ids_from_response("not-json"))

    def test_falls_back_to_generic_extractor(self):
        # An unexpected shape (no *ServerId keys) still yields the id via the
        # generic image extractor — here a bare mediaId-bearing nested dict.
        ids = upload_video_ids_from_response(
            {"result": {"mediaId": "1e123e08-456e-49ea-9a17-ad8fe288168e"}})
        self.assertIsNotNone(ids)
        self.assertEqual(ids["mediaId"], "1e123e08-456e-49ea-9a17-ad8fe288168e")
        self.assertNotIn("workflowId", ids)


class TestEditEndFrame(unittest.TestCase):
    """endFrameIndex = clip duration × 30 fps (web-app bundle: Math.round(d*30)).

    Captures agree: uploaded 4s clip → 120, generated 8s clip → 240. Frames past
    the clip's end make the edit job go ACTIVE → FAILED server-side.
    """

    # Real poll item shape from the upload-edit capture (seq 19/21).
    READY_POLL_ITEM = {
        "video": {
            "dimensions": {"width": 1280, "height": 720, "length": "4s"},
            "videoOffset": {"startOffset": "0s", "endOffset": "4s"},
        }
    }

    def test_end_frame_from_duration(self):
        self.assertEqual(video_edit_end_frame(4), 120)
        self.assertEqual(video_edit_end_frame(8.0), 240)
        self.assertEqual(video_edit_end_frame(4.5), 135)

    def test_default_when_duration_unknown(self):
        self.assertEqual(video_edit_end_frame(None), 240)
        self.assertEqual(video_edit_end_frame(0), 240)
        self.assertEqual(video_edit_end_frame(-1), 240)

    def test_duration_from_real_poll_item(self):
        self.assertEqual(video_duration_from_poll_item(self.READY_POLL_ITEM), 4.0)

    def test_duration_handles_fractional_and_missing(self):
        item = {"video": {"videoOffset": {"endOffset": "4.5s"}}}
        self.assertEqual(video_duration_from_poll_item(item), 4.5)
        self.assertIsNone(video_duration_from_poll_item({}))
        self.assertIsNone(video_duration_from_poll_item(None))
        self.assertIsNone(video_duration_from_poll_item({"video": {}}))

    def test_edit_payload_carries_real_end_frame(self):
        payload = build_video_edit_payload(
            prompt="убери скакалку",
            project_id=REAL_PROJECT_ID,
            captcha_token="tok",
            aspect="landscape",
            session_id=";1",
            batch_id=FAKE_BATCH_ID,
            source_media_id=REAL_MEDIA_ID,
            source_workflow_id=REAL_WORKFLOW_ID,
            end_frame_index=video_edit_end_frame(4.0),
        )
        self.assertEqual(
            payload["requests"][0]["videoInput"]["endFrameIndex"], 120)

    def test_videoref_carries_duration(self):
        ref = VideoRef(user_id=1, project_id=REAL_PROJECT_ID,
                       media_id=REAL_MEDIA_ID, duration_s=4.0)
        self.assertEqual(ref.duration_s, 4.0)
        bare = VideoRef(user_id=1, project_id=REAL_PROJECT_ID, media_id=REAL_MEDIA_ID)
        self.assertIsNone(bare.duration_s)


class TestCreditsResponse(unittest.TestCase):
    """Confirmed against a real /v1/credits capture (free-tier account)."""

    REAL_RESPONSE = {
        "credits": 50,
        "userPaygateTier": "PAYGATE_TIER_NOT_PAID",
        "sku": "G1_FREEMIUM",
        "serviceTier": "SERVICE_TIER_ENTRY",
        "subscriptionCredits": 50,
    }

    def test_endpoint_and_key_present(self):
        self.assertEqual(CREDITS_ENDPOINT, "https://aisandbox-pa.googleapis.com/v1/credits")
        self.assertTrue(FLOW_BROWSER_API_KEY.startswith("AIzaSy"))

    def test_parses_real_freemium_capture(self):
        parsed = parse_credits_response(self.REAL_RESPONSE)
        self.assertEqual(parsed["credits"], 50)
        self.assertEqual(parsed["subscription_credits"], 50)
        self.assertEqual(parsed["tier"], "PAYGATE_TIER_NOT_PAID")
        self.assertEqual(parsed["sku"], "G1_FREEMIUM")
        self.assertFalse(parsed["is_paid"])

    def test_paid_tier_heuristic(self):
        paid = dict(self.REAL_RESPONSE, userPaygateTier="PAYGATE_TIER_PAID")
        self.assertTrue(parse_credits_response(paid)["is_paid"])

    def test_malformed_response_returns_none(self):
        self.assertIsNone(parse_credits_response({}))
        self.assertIsNone(parse_credits_response(None))
        self.assertIsNone(parse_credits_response({"error": "nope"}))


if __name__ == "__main__":
    unittest.main()
