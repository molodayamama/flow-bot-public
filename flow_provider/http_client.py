"""Direct HTTP adapter for Google Flow image and video operations."""
from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import time

import aiohttp

from flow_core import (
    DEFAULT_IMAGE_MODEL,
    IMAGE_UPSAMPLE_ENDPOINT,
    VIDEO_CONCAT_ENDPOINT,
    VIDEO_CONCAT_POLL_INTERVAL,
    VIDEO_CONCAT_POLL_MAX,
    VIDEO_CONCAT_STATUS_ENDPOINT,
    VIDEO_EDIT_ENDPOINT,
    VIDEO_ENDPOINT as VIDEO_GEN_ENDPOINT,
    VIDEO_EXTEND_ENDPOINT,
    VIDEO_FRAMES_ENDPOINT,
    VIDEO_POLL_ENDPOINT,
    VIDEO_POLL_INTERVAL,
    VIDEO_POLL_TIMEOUT,
    VIDEO_REFERENCE_ENDPOINT,
    VIDEO_STATUS_FAILED,
    VIDEO_STATUS_SUCCESSFUL,
    apply_request_capture,
    build_concat_payload,
    build_concat_status_payload,
    build_generation_payload,
    build_upsample_payload,
    build_video_edit_payload,
    build_video_extend_payload,
    build_video_frame_images,
    build_video_payload,
    build_video_poll_payload,
    build_video_reference_images,
    check_video_poll_status,
    extract_agent_text,
    flow_scene_create_url,
    flow_scene_workflows_url,
    loads_xssi,
    parse_agent_response,
    parse_concat_operation_name,
    parse_concat_status,
    parse_scene_segments,
    parse_upsample_response,
    parse_video_gen_response,
    parse_video_scene_id,
    video_edit_end_frame,
    video_frames_model_key,
    video_media_redirect_url,
    video_model_key,
    video_model_meta,
    video_reference_model_key,
)

import flow_copy
from flow_provider.interfaces import FlowSession
from flow_provider.request_policy import (
    AGENT_RECAPTCHA_ACTION,
    RECAPTCHA_ACTIONS,
    VIDEO_GEN_403_BACKOFF_SEC,
    VIDEO_GEN_MAX_ATTEMPTS,
    VIDEO_RECAPTCHA_ACTION,
    build_flow_headers,
)
from flow_provider.runtime_config import (
    API_PROXY_URL,
    _effective_proxy_url,
    _video_failure_reason,
    log,
)


class FlowHttpClient:
    """
    Делает прямые HTTP запросы к Google API с токенами из живого браузера.
    Быстрее чем ждать пока браузер сам нарисует результат.
    """

    API_BASE = "https://aisandbox-pa.googleapis.com/v1"

    def __init__(self, keeper: FlowSession):
        self.keeper = keeper

    def _api_proxy(self) -> str | None:
        raw = (
            self.keeper.api_proxy_url
            if self.keeper.api_proxy_url is not None
            else API_PROXY_URL
        )
        return _effective_proxy_url(raw) or None

    def _build_headers(self, session: dict) -> dict:
        return build_flow_headers(session)

    @staticmethod
    def _video_ab_preview(text: str, limit: int = 240) -> str:
        if not text:
            return ""
        cleaned = re.sub(r"[\r\n\t]+", " ", str(text))
        cleaned = re.sub(r"(ya29\.|Bearer\s+|session-token)[^\s\"']+", r"\1***", cleaned)
        return cleaned[:limit]

    async def video_transport_ab_test(
        self,
        *,
        prompt: str,
        model_key: str = "omni-flash-4s",
        aspect: str = "landscape",
        order: str = "direct_first",
        pause_sec: float = 4.0,
        project_id: str | None = None,
        transports: list[str] | None = None,
    ) -> dict:
        """Costly admin diagnostic: compare direct HTTP and browser fetch video submit.

        This intentionally submits real text-to-video jobs. It does not poll or
        download outputs; the diagnostic target is initial submit acceptance
        (not final generation quality).
        """
        import uuid as _uuid
        import json as _json

        session = await self.keeper.get_session()
        if not session["bearer"]:
            return {"error": "missing_bearer", "arms": []}
        project_id = project_id or session.get("project_id")
        if not project_id:
            return {"error": "missing_project_id", "arms": []}

        headers = self._build_headers(session)
        proxy = self._api_proxy()
        action = VIDEO_RECAPTCHA_ACTION
        sess_id = f";{int(time.time() * 1000)}"
        transports = [str(t) for t in (transports or ["direct_http", "browser_fetch"])]
        transports = [t for t in transports if t in {"direct_http", "browser_fetch"}]
        if not transports:
            transports = ["direct_http"]
        if order == "browser_first" and transports == ["direct_http", "browser_fetch"]:
            transports.reverse()

        arms: list[dict] = []
        for idx, transport in enumerate(transports):
            if idx and pause_sec > 0:
                await asyncio.sleep(pause_sec)
            arm_started = time.time()
            captcha_token = await self.keeper.solve_captcha(action)
            if not captcha_token:
                arms.append({
                    "transport": transport,
                    "ok": False,
                    "status": None,
                    "error": "captcha_unavailable",
                    "duration_ms": int((time.time() - arm_started) * 1000),
                })
                continue

            payload = build_video_payload(
                prompt=prompt,
                project_id=project_id,
                captcha_token=captcha_token,
                aspect=aspect,
                model_key=model_key,
                session_id=sess_id,
                batch_id=str(_uuid.uuid4()),
            )
            status: int | None = None
            text = ""
            error = ""
            try:
                if transport == "direct_http":
                    async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                        async with http.post(
                            VIDEO_GEN_ENDPOINT,
                            headers=headers,
                            json=payload,
                            proxy=proxy,
                            timeout=aiohttp.ClientTimeout(total=75),
                        ) as resp:
                            status = resp.status
                            text = await resp.text()
                else:
                    resp = await self.keeper.post_json_via_browser(
                        VIDEO_GEN_ENDPOINT,
                        headers,
                        payload,
                        timeout_ms=75_000,
                    )
                    if isinstance(resp, dict):
                        status = int(resp.get("status") or 0)
                        text = str(resp.get("text") or "")
                    else:
                        error = "browser_post_failed"
            except Exception as exc:  # noqa: BLE001 - diagnostic result, no secrets
                error = exc.__class__.__name__

            parsed = {}
            if status == 200 and text:
                try:
                    info = parse_video_gen_response(_json.loads(text)) or {}
                    parsed = {
                        "accepted": bool(info),
                        "media_id_present": bool(info.get("media_id")),
                        "project_id_present": bool(info.get("project_id")),
                    }
                except Exception:
                    parsed = {"accepted": False, "parse_error": True}

            arms.append({
                "transport": transport,
                "ok": status == 200 and not error,
                "status": status,
                "error": error or None,
                "duration_ms": int((time.time() - arm_started) * 1000),
                "response": parsed,
                "body_preview": "" if status == 200 else self._video_ab_preview(text),
            })

        return {
            "account": self.keeper.account_id,
            "model_key": model_key,
            "aspect": aspect,
            "order": transports,
            "prompt_chars": len(prompt or ""),
            "arms": arms,
            "summary": {
                arm["transport"]: {"status": arm.get("status"), "ok": arm.get("ok")}
                for arm in arms
            },
        }

    async def create_agent_session(self, project_id: str | None = None) -> str | None:
        """Create a fresh flowCreationAgent session for the given project.

        ``POST /flowCreationAgent/sessions?projectId=<raw>`` with an empty body
        returns ``sessionInfo.agentSessionId``. A fresh session per request keeps
        users' agent conversations isolated (no shared/global session state).
        ``project_id`` must be the user's per-user project (the account-level
        session project is often empty when PER_USER_PROJECTS is on)."""
        for attempt in range(2):
            session = await self.keeper.get_session()
            if not session["bearer"]:
                return None
            proj_raw = str(project_id or session.get("project_id") or "")
            if not proj_raw:
                return None
            headers = dict(self._build_headers(session))
            headers["Content-Type"] = "application/json"
            headers["Accept"] = "*/*"
            url = f"{self.API_BASE}/flowCreationAgent/sessions?projectId={proj_raw}"
            try:
                async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                    async with http.post(
                        url, headers=headers, data=b"{}", proxy=self._api_proxy(),
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        status = resp.status
                        if status == 401 and attempt == 0:
                            await self.keeper._refresh_bearer()
                            continue
                        if status != 200:
                            return None
                        data = await resp.json(content_type=None)
                sid = ((data or {}).get("sessionInfo") or {}).get("agentSessionId")
                return str(sid) if sid else None
            except Exception:  # noqa: BLE001 - best effort, caller handles None
                return None
        return None

    async def improve_prompt(
        self,
        text: str,
        *,
        action: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        agent_session_id: str | None = None,
        turn_number: int = 1,
        timeout_total: float = 90.0,
        debug: bool = False,
    ) -> dict:
        """Call Flow's ``flowCreationAgent:streamChat`` to improve a prompt.

        Reuses the live bearer/project/cookies and the browser-JS reCAPTCHA
        solver. Returns sanitized fields only (status, parsed variants/single,
        message) — never bearer/cookie/token values. The reСАPTCHA ``action`` is
        a parameter so the admin discovery probe can try candidates.
        """
        session = await self.keeper.get_session()
        if not session["bearer"]:
            return {"error": "missing_bearer"}
        project_id = project_id or session.get("project_id")
        if not project_id:
            return {"error": "missing_project_id"}

        # streamChat requires a real session id (a random UUID gets an empty
        # errorEvent). Create a fresh session per request unless one is supplied.
        if not agent_session_id:
            agent_session_id = await self.create_agent_session(project_id=project_id)
        if not agent_session_id:
            return {"error": "session_create_failed"}

        action = action or AGENT_RECAPTCHA_ACTION
        captcha_token = await self.keeper.solve_captcha(action)
        if not captcha_token:
            return {"error": "captcha_unavailable", "action": action}

        headers = dict(self._build_headers(session))
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "text/event-stream, text/event-stream"

        proj = str(project_id)
        if not proj.startswith("projects/"):
            proj = f"projects/{proj}"
        body = {
            "agentSessionId": agent_session_id,
            "agentClientContext": {
                "projectId": proj,
                "clientSessionId": session_id or f";{int(time.time() * 1000)}",
                "recaptchaContext": {
                    "token": captcha_token,
                    "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
                },
                "turnNumber": int(turn_number),
            },
            "userMessage": {"userPrompt": {"parts": [{"text": str(text or "")}]}},
        }
        url = f"{self.API_BASE}/flowCreationAgent:streamChat?alt=sse"

        status: int | None = None
        raw = ""
        error = ""
        try:
            async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                async with http.post(
                    url,
                    headers=headers,
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                    proxy=self._api_proxy(),
                    timeout=aiohttp.ClientTimeout(total=timeout_total),
                ) as resp:
                    status = resp.status
                    raw = await resp.text()
        except Exception as exc:  # noqa: BLE001 - return JSON, never raw secrets
            error = exc.__class__.__name__

        parsed = (
            parse_agent_response(raw)
            if status == 200 and raw
            else {"variants": [], "single": None, "message": ""}
        )
        out = {
            "ok": status == 200 and not error and bool(parsed["variants"] or parsed["single"]),
            "status": status,
            "action": action,
            "error": error or None,
            "variants": parsed["variants"],
            "single": parsed["single"],
            "message": parsed["message"][:600],
            "agent_session_id": agent_session_id,
            "turn_number": int(turn_number),
            "body_preview": "" if status == 200 else self._video_ab_preview(raw),
        }
        if debug:
            out["agent_text_preview"] = self._video_ab_preview(extract_agent_text(raw), limit=2000)
            out["raw_len"] = len(raw or "")
            out["raw_preview"] = self._video_ab_preview(raw, limit=2000)
        return out

    async def agent_session_call(
        self, *, method: str = "GET", suffix: str = "", json_body: dict | None = None,
        project_id: str | None = None, timeout_total: float = 45.0,
    ) -> dict:
        """Explore/operate the flowCreationAgent/sessions endpoints (bearer only,
        no captcha). ``suffix`` is appended after ``/sessions`` (e.g. ``/<id>``).
        Returns sanitized status + raw preview."""
        session = await self.keeper.get_session()
        if not session["bearer"]:
            return {"error": "missing_bearer"}
        headers = dict(self._build_headers(session))
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "*/*"
        # The collection needs ?projectId=<raw uuid> (confirmed by browser
        # capture); a specific /<id> path does not.
        if "projectId=" not in suffix and not suffix.startswith("/"):
            proj_raw = str(project_id or session.get("project_id") or "")
            if proj_raw:
                sep = "&" if "?" in suffix else "?"
                suffix = f"{suffix}{sep}projectId={proj_raw}"
        url = f"{self.API_BASE}/flowCreationAgent/sessions{suffix}"
        status: int | None = None
        raw = ""
        error = ""
        try:
            async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                req = http.request(
                    method.upper(), url, headers=headers,
                    data=(json.dumps(json_body, ensure_ascii=False).encode("utf-8")
                          if json_body is not None else None),
                    proxy=self._api_proxy(),
                    timeout=aiohttp.ClientTimeout(total=timeout_total),
                )
                async with req as resp:
                    status = resp.status
                    raw = await resp.text()
        except Exception as exc:  # noqa: BLE001 - diagnostic, return JSON
            error = exc.__class__.__name__
        return {
            "status": status,
            "error": error or None,
            "raw_len": len(raw or ""),
            "raw_preview": self._video_ab_preview(raw, limit=2000),
        }

    async def generate_images(
        self,
        prompt: str,
        aspect_ratio: str = "landscape",
        num_images: int = 4,
        progress_cb=None,
        project_id: str | None = None,
        image_inputs: list | None = None,
        allow_browser_fallback: bool = True,
        image_model: str = DEFAULT_IMAGE_MODEL,
    ) -> dict:
        """Сгенерировать (или, при ``image_inputs``, отредактировать) изображения.

        ``project_id`` — проект конкретного Telegram-пользователя; если не задан,
        берётся проект текущей сессии (старое поведение). ``image_inputs``
        ссылается на исходное изображение для редактирования (см.
        ``flow_core.build_image_inputs``). При редактировании браузерный фолбэк
        отключают, чтобы не выдать вместо правки несвязанную картинку.
        """
        session = await self.keeper.get_session()

        if not session["bearer"]:
            log.warning("Bearer не получен — использую браузерный режим")
            if allow_browser_fallback:
                return await self.keeper.generate_via_browser(prompt)
            return {"error": "Нет Bearer-токена. Попробуйте позже."}

        project_id = project_id or session["project_id"]
        if not project_id:
            log.warning("project_id не получен — использую браузерный режим")
            # Нет проекта у сессии аккаунта = логин протух → пометить на релогин
            # и вывести из ротации (см. AccountPool.is_available).
            self.keeper._flag_needs_relogin(True)
            if allow_browser_fallback:
                return await self.keeper.generate_via_browser(prompt)
            return {"error": "Не удалось определить проект. Попробуйте позже."}

        url = f"{self.API_BASE}/projects/{project_id}/flowMedia:batchGenerateImages"
        seed = random.randint(100_000, 999_999)
        sess_id = f";{int(time.time() * 1000)}"

        # Ротация actions: пробуем каждый action пока Google не примет
        actions = list(RECAPTCHA_ACTIONS)
        saw_403 = False
        saw_unusual_activity = False

        for idx, action in enumerate(actions):
            if progress_cb:
                await progress_cb(flow_copy.msg("working"))
            captcha_token = await self.keeper.solve_captcha(action)

            if not captcha_token:
                continue

            payload = build_generation_payload(
                prompt=prompt,
                project_id=project_id,
                captcha_token=captcha_token,
                aspect=aspect_ratio,
                num_images=num_images,
                seed=seed,
                session_id=sess_id,
                image_inputs=image_inputs,
                image_model=image_model,
            )

            if image_inputs:
                if progress_cb:
                    await progress_cb(flow_copy.msg("applying_edit"))
            elif progress_cb:
                await progress_cb(flow_copy.msg("generating_n", n=num_images))

            try:
                async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                    proxy = self._api_proxy()
                    async with http.post(
                        url,
                        headers=self._build_headers(session),
                        json=payload,
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=75),
                    ) as resp:
                        status = resp.status
                        text = await resp.text()
                        log.info(f"📡 HTTP ответ: {status} (action={action})")

            except Exception as exc:
                log.error("❌ HTTP ошибка: %s", exc.__class__.__name__)
                # Текст исключения может содержать хосты бэкенда — юзеру нейтрально.
                return {"error": flow_copy.msg("gen_failed")}

            # 200 — успех
            if status == 200:
                import json as _json

                try:
                    return _json.loads(text)
                except Exception:
                    return {"error": flow_copy.msg("parse_failed")}

            # 401 — Bearer протух, обновляем и повторяем
            if status == 401:
                log.warning("🔑 Bearer устарел, обновляю...")
                await self.keeper._refresh_bearer()
                session = await self.keeper.get_session()
                continue

            # 403 — капча не прошла, пробуем следующий action
            if status == 403:
                saw_403 = True
                if "PUBLIC_ERROR_UNUSUAL_ACTIVITY" in text or "unusual activity" in text.lower():
                    saw_unusual_activity = True
                log.warning("⚠️ HTTP 403 action=%s (%s/%s)", action, idx + 1, len(actions))
                continue

            if status == 429:
                return {"error": flow_copy.msg("rate_limited")}

            if status == 400:
                log.warning("⚠️ Prompt rejected (400)")
                return {"error": flow_copy.msg("prompt_rejected"), "error_type": "prompt_rejected"}

            log.error("❌ Неизвестный статус %s", status)
            return {"error": flow_copy.msg("service_error", status=status)}

        # Все actions провалились
        if saw_unusual_activity:
            # Флаг уровня аккаунта/сессии (см. комментарий у RECAPTCHA_ACTIONS) —
            # браузерный фолбэк упрётся в то же ограничение и просто потратит
            # ~25-50с на ожидание textarea, которая не появится. Сигналим
            # account_risk сразу, чтобы вызывающий код ушёл в кулдаун и
            # фейловернулся на другой аккаунт без лишнего ожидания.
            log.warning("Все actions провалились (unusual_activity) — без браузерного фолбэка")
            return {
                "error": flow_copy.msg("rate_limited"),
                "account_risk": "unusual_activity",
            }
        if allow_browser_fallback:
            log.warning("Все actions провалились, фолбек в браузер")
            return await self.keeper.generate_via_browser(prompt)
        log.warning("Все actions провалились (без браузерного фолбэка)")
        if saw_403:
            return {"error": flow_copy.msg("rate_limited")}
        return {"error": flow_copy.msg("gen_failed")}

    async def run_captured_request(
        self, capture: dict, media_id: str, project_id: str | None, progress_cb=None
    ) -> dict:
        """Воспроизвести изученный запрос НАСТОЯЩЕГО апскейла для картинки.

        Подставляет свежие captcha/project/session/media в захваченный шаблон и
        шлёт его. Возвращает разобранный JSON ответа сервиса или ``{"error": ...}``.
        """
        session = await self.keeper.get_session()
        if not session["bearer"]:
            return {"error": flow_copy.msg("upscale_unavailable")}
        project_id = project_id or session["project_id"]
        sess_id = f";{int(time.time() * 1000)}"

        for idx, action in enumerate(RECAPTCHA_ACTIONS):
            if progress_cb:
                await progress_cb(flow_copy.msg("upscaling"))
            captcha_token = await self.keeper.solve_captcha(action)
            if not captcha_token:
                continue
            built = apply_request_capture(
                capture,
                media_id=media_id,
                captcha=captcha_token,
                project_id=project_id or "",
                session_id=sess_id,
            )
            if not built:
                return {"error": flow_copy.msg("upscale_unavailable")}
            url, body = built
            try:
                async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                    proxy = self._api_proxy()
                    async with http.post(
                        url,
                        headers=self._build_headers(session),
                        json=body,
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=180),
                    ) as resp:
                        status = resp.status
                        text = await resp.text()
                        log.info(f"📡 UPSCALE ответ: {status} (action={action})")
            except Exception as exc:
                log.error("❌ UPSCALE сеть: %s", exc.__class__.__name__)
                return {"error": flow_copy.msg("gen_failed")}

            if status == 200:
                parsed = loads_xssi(text)
                return parsed if isinstance(parsed, dict) else {"error": flow_copy.msg("parse_failed")}
            if status == 401:
                await self.keeper._refresh_bearer()
                session = await self.keeper.get_session()
                continue
            if status == 403:
                log.warning("⚠️ UPSCALE 403 (%s)", idx + 1)
                continue
            if status == 429:
                return {"error": flow_copy.msg("rate_limited")}
            log.error("❌ UPSCALE статус %s", status)
            return {"error": flow_copy.msg("service_error", status=status)}

        return {"error": flow_copy.msg("upscale_unavailable")}

    async def upsample_image(
        self, media_id: str, project_id: str | None, *, progress_cb=None
    ) -> dict:
        """Родной серверный апскейл картинки (UI «Upscaled x2»).

        Синхронный POST ``flow/upsampleImage`` — ответ содержит готовую увеличенную
        картинку base64 (``encodedImage``), поллинг не нужен. Возвращает
        ``{"image_bytes": bytes}`` или ``{"error": ...}``. Контракт сверен из
        реального захвата (а не промпт-доработка, как «Чёткость ×2»).
        """
        session = await self.keeper.get_session()
        if not session["bearer"]:
            return {"error": flow_copy.msg("upscale_unavailable")}
        project_id = project_id or session["project_id"]
        sess_id = f";{int(time.time() * 1000)}"

        for idx, action in enumerate(RECAPTCHA_ACTIONS):
            if progress_cb:
                await progress_cb(flow_copy.msg("upscaling"))
            captcha_token = await self.keeper.solve_captcha(action)
            if not captcha_token:
                continue
            payload = build_upsample_payload(
                media_id=media_id,
                project_id=project_id or "",
                captcha_token=captcha_token,
                session_id=sess_id,
            )
            try:
                async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                    proxy = self._api_proxy()
                    async with http.post(
                        IMAGE_UPSAMPLE_ENDPOINT,
                        headers=self._build_headers(session),
                        json=payload,
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=180),
                    ) as resp:
                        status = resp.status
                        text = await resp.text()
                        log.info(f"📡 UPSAMPLE ответ: {status} (action={action})")
            except Exception as exc:
                log.error("❌ UPSAMPLE сеть: %s", exc.__class__.__name__)
                return {"error": flow_copy.msg("gen_failed")}

            if status == 200:
                import json as _json
                import base64
                try:
                    data = _json.loads(text)
                except Exception:
                    return {"error": flow_copy.msg("parse_failed")}
                enc = parse_upsample_response(data)
                if not enc:
                    return {"error": flow_copy.msg("upscale_unavailable")}
                try:
                    return {"image_bytes": base64.b64decode(enc)}
                except Exception:
                    return {"error": flow_copy.msg("parse_failed")}
            if status == 401:
                await self.keeper._refresh_bearer()
                session = await self.keeper.get_session()
                continue
            if status == 403:
                log.warning("⚠️ UPSAMPLE 403 (%s)", idx + 1)
                continue
            if status == 429:
                return {"error": flow_copy.msg("rate_limited")}
            log.error("❌ UPSAMPLE статус %s", status)
            return {"error": flow_copy.msg("service_error", status=status)}

        return {"error": flow_copy.msg("upscale_unavailable")}

    async def prepare_video_extend_scene(
        self,
        *,
        project_id: str | None,
        workflow_id: str | None,
    ) -> str | None:
        """Create/read the Flow scene required by native video Extend."""
        if not (project_id and workflow_id):
            return None

        session = await self.keeper.get_session()
        if not session["bearer"]:
            return None

        headers = self._build_headers(session)
        proxy = self._api_proxy()

        try:
            async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                async with http.post(
                    flow_scene_create_url(project_id),
                    headers=headers,
                    json={"workflowIds": [workflow_id]},
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        log.warning("video scene create -> %s", resp.status)
                        return None
                    create_data = await resp.json(content_type=None)

                scene_id = parse_video_scene_id(create_data)
                if not scene_id:
                    return None

                async with http.get(
                    flow_scene_workflows_url(scene_id, project_id),
                    headers=headers,
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        workflows_data = await resp.json(content_type=None)
                        scene_id = parse_video_scene_id(workflows_data) or scene_id
                    else:
                        log.warning(f"video scene workflows -> {resp.status}")
                return scene_id
        except Exception as exc:
            log.warning("video scene prep failed: %s", exc.__class__.__name__)
            return None

    async def fetch_full_extended_video(
        self, scene_id: str, project_id: str
    ) -> bytes | None:
        """Return the full stitched video for an extended scene, or None.

        Replays the service's own "download full" path: GET scene workflows to
        learn the ordered segment timeline, POST runVideoFxConcatenation, poll
        runVideoFxCheckConcatenationStatus, and base64-decode the resulting
        ``encodedVideo``. No local ffmpeg involved.
        """
        session = await self.keeper.get_session()
        if not session["bearer"]:
            return None
        headers = self._build_headers(session)
        proxy = self._api_proxy()
        try:
            async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                async with http.get(
                    flow_scene_workflows_url(scene_id, project_id),
                    headers=headers,
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json(content_type=None)

                segments = parse_scene_segments(data)
                if len(segments) < 2:
                    return None  # nothing to stitch; caller falls back to the segment

                async with http.post(
                    VIDEO_CONCAT_ENDPOINT,
                    headers=headers,
                    json=build_concat_payload(segments),
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        log.warning(f"concat start -> {resp.status}")
                        return None
                    op_data = await resp.json(content_type=None)

                op_name = parse_concat_operation_name(op_data)
                if not op_name:
                    return None

                status_payload = build_concat_status_payload(op_name)
                for _ in range(VIDEO_CONCAT_POLL_MAX):
                    await asyncio.sleep(VIDEO_CONCAT_POLL_INTERVAL)
                    async with http.post(
                        VIDEO_CONCAT_STATUS_ENDPOINT,
                        headers=headers,
                        json=status_payload,
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status != 200:
                            continue
                        st_data = await resp.json(content_type=None)
                    status, encoded = parse_concat_status(st_data)
                    if status == VIDEO_STATUS_SUCCESSFUL:
                        if not encoded:
                            return None
                        import base64
                        try:
                            return base64.b64decode(encoded)
                        except Exception:
                            log.exception("concat encodedVideo decode failed")
                            return None
                    if status == VIDEO_STATUS_FAILED:
                        log.warning("concat job failed")
                        return None
                log.warning("concat polling timed out")
                return None
        except Exception as exc:
            log.warning("fetch_full_extended_video failed: %s", exc.__class__.__name__)
            return None

    async def generate_video(
        self,
        prompt: str,
        model_key: str = "omni-flash-4s",
        aspect: str = "landscape",
        project_id: str | None = None,
        reference_sources: list[dict] | None = None,
        start_source: dict | None = None,
        end_source: dict | None = None,
        operation: str = "generate",
        source_media_id: str | None = None,
        source_workflow_id: str | None = None,
        source_scene_id: str | None = None,
        source_duration_s: float | None = None,
        progress_cb=None,
    ) -> dict:
        """Сгенерировать видео через асинхронный Flow video API.

        Шаги:
          1. POST ``video:batchAsyncGenerateVideoText`` — получаем media_id.
          2. Раз в ``VIDEO_POLL_INTERVAL`` секунд POST
             ``video:batchCheckAsyncVideoGenerationStatus`` — ждём
             ``MEDIA_GENERATION_STATUS_SUCCESSFUL``.
          3. Возвращаем ``{"media_id": ..., "project_id": ...}`` (URL видео
             нужно получить отдельно — см. ``fetch_video_url``).

        Возвращает ``{"error": ...}`` при неудаче.
        """
        # Все три режима захвачены и включены: text / Frames (старт-финиш) /
        # Ingredients (reference-to-video). Эндпоинт и payload выбираются по входу.
        import uuid as _uuid

        session = await self.keeper.get_session()
        if not session["bearer"]:
            return {"error": "Нет Bearer-токена для видео"}

        project_id = project_id or session.get("project_id")
        if not project_id:
            return {"error": "Нет project_id для видео"}

        sess_id  = f";{int(time.time() * 1000)}"
        headers  = self._build_headers(session)
        proxy    = self._api_proxy()
        reference_images = build_video_reference_images(reference_sources)
        start_image, end_image = build_video_frame_images(start_source, end_source)
        operation = (operation or "generate").strip().lower()
        is_edit = operation == "edit"
        is_extend = operation == "extend"
        is_frames = bool(start_image or end_image) and not (is_edit or is_extend)
        is_reference = bool(reference_images) and not is_frames and not (is_edit or is_extend)
        if is_edit:
            if not (source_media_id and source_workflow_id):
                return {"error": flow_copy.msg("vid_extend_unavailable")}
            gen_endpoint = VIDEO_EDIT_ENDPOINT
            endpoint_name = "Edit"
        elif is_extend:
            if not (source_media_id and source_scene_id):
                return {"error": flow_copy.msg("vid_extend_unavailable")}
            gen_endpoint = VIDEO_EXTEND_ENDPOINT
            endpoint_name = "Extend"
        elif is_frames:
            gen_endpoint = VIDEO_FRAMES_ENDPOINT
            endpoint_name = "Frames"
        elif is_reference:
            gen_endpoint = VIDEO_REFERENCE_ENDPOINT
            endpoint_name = "Reference"
        else:
            gen_endpoint = VIDEO_GEN_ENDPOINT
            endpoint_name = "Text"
        if is_frames:
            effective_model_key = video_frames_model_key(model_key)
        elif is_reference:
            effective_model_key = video_reference_model_key(model_key, aspect)
        elif is_edit:
            effective_model_key = "abra_edit"
        else:
            effective_model_key = video_model_key(model_key)
        model_family = str((video_model_meta(model_key) or {}).get("family") or "unknown")

        def _submit_meta(data: dict) -> dict:
            data.update({
                "model_key": effective_model_key,
                "model_family": model_family,
                "endpoint": endpoint_name.lower(),
                "mode": endpoint_name.lower(),
                "transport": "browser_fetch" if browser_fallback_used else "direct_http",
                "attempts": attempts_made,
                "had_403": had_403,
                "unusual_403": unusual_403,
                "browser_fallback": browser_fallback_used,
            })
            return data

        # Values-free request-shape trace for reference/frames diagnostics.
        if is_reference or is_frames:
            log.info(
                "🎬 r2v req account=%s endpoint=%s effective_model_key=%s "
                "aspect=%s reference_count=%s frames=%s",
                self.keeper.account_id, endpoint_name, effective_model_key,
                aspect, len(reference_images),
                bool(start_image or end_image),
            )

        # ── Шаг 1: капча + отправка (единственный верный action + ретраи) ──
        # action = VIDEO_GENERATION (подтверждён захватом). 403 = низкий score /
        # антифрод, поэтому при 403 ретраим тот же action со свежим токеном и
        # коротким бэкоффом; перебор неверных action'ов убран (он лишь усиливал
        # флаг аккаунта). Каждая попытка — свежий токен и свежий batchId.
        if progress_cb:
            await progress_cb("⏳ Отправляю запрос на генерацию видео…")

        gen_status: int | None = None
        gen_text = ""
        solved_any = False
        refreshed_after_403 = False
        refreshed_after_401 = False
        had_403 = False
        unusual_403 = False
        attempts_made = 0
        action = VIDEO_RECAPTCHA_ACTION
        browser_fallback_used = False

        def _build_submit_payload(captcha_token: str) -> dict:
            batch_id = str(_uuid.uuid4())
            if is_edit:
                return build_video_edit_payload(
                    prompt=prompt,
                    project_id=project_id,
                    captcha_token=captcha_token,
                    aspect=aspect,
                    session_id=sess_id,
                    batch_id=batch_id,
                    source_media_id=source_media_id or "",
                    source_workflow_id=source_workflow_id or "",
                    end_frame_index=video_edit_end_frame(source_duration_s),
                )
            if is_extend:
                return build_video_extend_payload(
                    prompt=prompt,
                    project_id=project_id,
                    captcha_token=captcha_token,
                    aspect=aspect,
                    model_key=model_key,
                    session_id=sess_id,
                    batch_id=batch_id,
                    source_media_id=source_media_id or "",
                    scene_id=source_scene_id or "",
                )
            return build_video_payload(
                prompt=prompt,
                project_id=project_id,
                captcha_token=captcha_token,
                aspect=aspect,
                model_key=model_key,
                session_id=sess_id,
                batch_id=batch_id,
                reference_images=reference_images,
                start_image=start_image,
                end_image=end_image,
            )

        for _attempt in range(VIDEO_GEN_MAX_ATTEMPTS):
            posted = False
            for _auth_attempt in range(2):
                captcha_token = await self.keeper.solve_captcha(action)
                if not captcha_token:
                    break
                solved_any = True
                posted = True
                payload = _build_submit_payload(captcha_token)
                try:
                    async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                        async with http.post(
                            gen_endpoint,
                            headers=headers,
                            json=payload,
                            proxy=proxy,
                            timeout=aiohttp.ClientTimeout(total=60),
                        ) as resp:
                            gen_status = resp.status
                            gen_text   = await resp.text()
                except Exception as exc:
                    log.error("🎬 video network error: %s", exc.__class__.__name__)
                    return {"error": flow_copy.msg("vid_gen_failed")}

                if gen_status == 401 and not refreshed_after_401:
                    log.warning("🎬 video → 401 (action=%s), refreshing bearer and retrying", action)
                    refreshed_after_401 = True
                    await self.keeper._refresh_bearer()
                    session = await self.keeper.get_session()
                    headers = self._build_headers(session)
                    continue
                break

            if not posted:
                continue
            attempts_made = _attempt + 1

            if gen_status == 403:
                had_403 = True
                if "PUBLIC_ERROR_UNUSUAL_ACTIVITY" in gen_text or "unusual activity" in gen_text.lower():
                    unusual_403 = True
                log.warning(
                    "🎬 video → 403 (попытка %d/%d, score/антифрод), свежий токен",
                    _attempt + 1, VIDEO_GEN_MAX_ATTEMPTS,
                )
                if not refreshed_after_403:
                    refreshed_after_403 = True
                    await self.keeper._refresh_bearer()
                    session = await self.keeper.get_session()
                    headers = self._build_headers(session)
                # Нарастающий бэкофф + jitter перед СЛЕДУЮЩЕЙ попыткой; после
                # последней 403 не спим зря (всё равно выходим из цикла).
                if _attempt < VIDEO_GEN_MAX_ATTEMPTS - 1:
                    backoff = VIDEO_GEN_403_BACKOFF_SEC * (_attempt + 1) + random.uniform(1.0, 4.0)
                    await asyncio.sleep(backoff)
                continue
            log.info(f"🎬 video {endpoint_name} → {gen_status} (action={action})")
            break

        if not solved_any:
            return _submit_meta({"error": "Не удалось решить капчу для видео"})
        if gen_status == 401:
            return _submit_meta({
                "error": "Bearer устарел, попробуйте ещё раз",
                "account_risk": "video_auth",
            })
        if gen_status == 429:
            return _submit_meta({
                "error": flow_copy.msg("rate_limited"),
            })
        if gen_status == 403:
            return _submit_meta({
                "error": "Сервис отклонил запрос видео (403): низкий score/антифрод reCAPTCHA.",
                "account_risk": "video_recaptcha_403",
                "had_403": True,
                "unusual_403": unusual_403,
            })
        if gen_status != 200:
            log.warning("🎬 video %s non-200 status=%s", endpoint_name, gen_status)
            return _submit_meta({"error": flow_copy.msg("service_error", status=gen_status)})

        try:
            import json as _json
            gen_data = _json.loads(gen_text)
        except Exception:
            return _submit_meta({"error": "Не удалось разобрать ответ генерации видео"})

        media_info = parse_video_gen_response(gen_data)
        if not media_info:
            log.warning("🎬 video %s 200 but no media_id", endpoint_name)
            return _submit_meta({"error": "media_id не найден в ответе"})

        media_id   = media_info["media_id"]
        project_id = media_info["project_id"]
        workflow_id = media_info.get("workflow_id")
        scene_id = media_info.get("scene_id") or (source_scene_id if is_extend else None)
        log.info("🎬 видео принято, ожидаю готовности…")

        # ── Шаг 2: polling ─────────────────────────────────────────────
        poll_payload = build_video_poll_payload(media_id, project_id)
        deadline     = time.time() + VIDEO_POLL_TIMEOUT
        poll_num     = 0

        while time.time() < deadline:
            await asyncio.sleep(VIDEO_POLL_INTERVAL)
            poll_num += 1

            # Анимация статусных фраз во время ожидания живёт в _video_generate_and_send
            # (фоновая задача, обновление каждые 2.5 сек) — здесь её больше не дублируем,
            # чтобы две правки одного сообщения не конфликтовали.

            try:
                async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                    async with http.post(
                        VIDEO_POLL_ENDPOINT,
                        headers=headers,
                        json=poll_payload,
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status != 200:
                            log.warning(f"⚠️ poll {poll_num} → {resp.status}")
                            continue
                        poll_data = await resp.json(content_type=None)
            except Exception as exc:
                log.warning("⚠️ poll %s ошибка: %s", poll_num, exc.__class__.__name__)
                continue

            status, poll_item = check_video_poll_status(poll_data)
            log.info(f"🎬 poll {poll_num}: {status}")

            if status == VIDEO_STATUS_SUCCESSFUL:
                if isinstance(poll_item, dict):
                    item_workflow_id = poll_item.get("workflowId")
                    if isinstance(item_workflow_id, str) and item_workflow_id:
                        workflow_id = item_workflow_id
                    item_scene_id = poll_item.get("sceneId")
                    if isinstance(item_scene_id, str) and item_scene_id:
                        scene_id = item_scene_id
                return _submit_meta({
                    "media_id":   media_id,
                    "project_id": project_id,
                    "workflow_id": workflow_id,
                    "scene_id":    scene_id,
                    "status":     "ok",
                })
            if status == VIDEO_STATUS_FAILED:
                # Причина из тела FAILED-итема: звук не сгенерился / модерация —
                # это контент-фейлы (не вина аккаунта), их показываем юзеру.
                reason = _video_failure_reason(poll_item)
                log.warning("🎬 FAILED item (reason=%s)", reason or "unknown")
                if reason == "audio_filtered":
                    return _submit_meta({"error": "audio filter", "failure": "audio_filtered"})
                if reason == "danger_filter":
                    return _submit_meta({"error": "danger filter", "failure": "danger_filter"})
                return _submit_meta({"error": "Генерация видео завершилась с ошибкой на стороне Google"})

        return _submit_meta({"error": f"Таймаут ({VIDEO_POLL_TIMEOUT}с): видео не готово"})

    async def wait_video_ready(
        self,
        media_id: str,
        project_id: str,
        timeout: float = 120,
        interval: float = 3,
    ) -> dict | None:
        """Дождаться готовности ЗАГРУЖЕННОГО видео (серверный транскод).

        Веб-приложение после upload поллит ``batchCheckAsyncVideoGenerationStatus``
        (PENDING → SUCCESSFUL, см. захват --upload-edit, seq 19/21) и лишь потом
        разрешает Edit. Правка до готовности завершается
        ``MEDIA_GENERATION_STATUS_FAILED`` на стороне сервиса.

        Возвращает poll-итем готового видео (в нём ``video.dimensions.length`` —
        реальная длительность клипа для endFrameIndex) или ``None``.
        """
        session = await self.keeper.get_session()
        if not session["bearer"] or not project_id:
            return None
        headers = self._build_headers(session)
        proxy = self._api_proxy()
        payload = build_video_poll_payload(media_id, project_id)
        deadline = time.time() + timeout
        poll_num = 0
        while time.time() < deadline:
            poll_num += 1
            try:
                async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                    async with http.post(
                        VIDEO_POLL_ENDPOINT,
                        headers=headers,
                        json=payload,
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status != 200:
                            log.warning(f"⚠️ upload-ready poll {poll_num} → {resp.status}")
                        else:
                            data = await resp.json(content_type=None)
                            status, item = check_video_poll_status(data)
                            log.info(f"⬆️ upload-ready poll {poll_num}: {status}")
                            if status == VIDEO_STATUS_SUCCESSFUL:
                                if not isinstance(item, dict):
                                    # Молчаливый {} маскировал бы битый ответ: без
                                    # poll-итема не узнать длительность клипа.
                                    log.warning(
                                        "⚠️ upload-ready: SUCCESSFUL, но poll-итем не dict (%s)",
                                        type(item).__name__,
                                    )
                                    return {}
                                return item
                            if status == VIDEO_STATUS_FAILED:
                                return None
            except Exception as exc:
                log.warning("⚠️ upload-ready poll %s ошибка: %s", poll_num, exc.__class__.__name__)
            await asyncio.sleep(interval)
        log.warning("⚠️ upload-ready: таймаут %sс", timeout)
        return None

    async def fetch_video_bytes(self, media_id: str) -> bytes | None:
        """Скачать готовое видео по ``media_id``.

        Фронт Flow резолвит media_id в файл через tRPC-редирект на labs.google
        (подтверждено по ``<video>.src`` страницы):
            GET https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=<id>
        Эндпоинт на хосте labs.google (НЕ API), авторизация — cookies браузерной
        сессии, не Bearer. Запрос 302-редиректит на реальные байты видео;
        aiohttp по умолчанию следует за редиректом.

        Возвращает байты видео или ``None`` при неудаче.
        """
        session = await self.keeper.get_session()
        cookies = session.get("cookies") or {}
        url = video_media_redirect_url(media_id)
        proxy = self._api_proxy()

        # Заголовки лёгкие: это запрос к фронту labs.google, не к API.
        headers = {
            "Accept": "*/*",
            "Referer": "https://labs.google/fx/tools/flow",
        }
        ua = session.get("headers", {}).get("user-agent")
        if ua:
            headers["User-Agent"] = ua

        try:
            async with aiohttp.ClientSession(cookies=cookies) as http:
                async with http.get(
                    url,
                    headers=headers,
                    proxy=proxy,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        log.warning("⚠️ fetch_video_bytes → %s", resp.status)
                        return None
                    data = await resp.read()
                    log.info("🎬 видео скачано: %s байт", len(data))
                    return data
        except Exception as exc:
            log.error("❌ fetch_video_bytes: %s", exc.__class__.__name__)
            return None
