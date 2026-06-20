"""Offline tests for the Flow creation-agent (prompt improver) SSE parser.

Fixtures mirror the real `flowCreationAgent:streamChat` stream shape captured by
the operator. They contain only A2UI content (no tokens/secrets)."""

import json
import unittest

import flow_core


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False)


def _agent_msg(text: str, *, partial: bool) -> dict:
    msg = {"agentMessage": {"response": {"text": text}, "responseId": "r"}}
    if partial:
        msg["partial"] = True
    return msg


# A2UI document with a 3-option MultipleChoice (the "give me variants" case).
_VARIANTS_A2UI = [
    {"beginRendering": {"surfaceId": "main", "root": "root-column"}},
    {"surfaceUpdate": {"surfaceId": "main", "components": [
        {"id": "root-column", "component": {"Column": {"children": {"explicitList": ["text-1", "opts"]}}}},
        {"id": "text-1", "component": {"Text": {"text": {"literalString":
            "Выберите одно из направлений для промпта:"}}}},
        {"id": "opts", "component": {"MultipleChoice": {"maxAllowedSelections": 1,
            "selections": {"path": "/selected_option"}, "options": [
                {"label": {"literalString": "**Реалистичное макро**\nКрупный план котёнка на лежанке, мягкий боке."},
                 "value": "realistic_macro"},
                {"label": {"literalString": "**Сказочный 3D-стиль**\nМилый котёнок с большими глазами, 3D-анимация."},
                 "value": "stylized_3d"},
                {"label": {"literalString": "**Классическое масло**\nУютная сцена в стиле масляной живописи."},
                 "value": "oil_painting"},
            ]}}},
    ]}},
]

# A2UI document with a single improved prompt inside a blockquote.
_SINGLE_A2UI = [
    {"surfaceUpdate": {"surfaceId": "main", "components": [
        {"id": "text-1", "component": {"Text": {"text": {"literalString":
            "Вот один из лучших вариантов:\n\n> **Кинематографичный макропортрет котёнка в мягкой лежанке, тёплый закатный свет.**\n\nХотите создать?"}}}},
    ]}},
]


def _fenced(a2ui: list) -> str:
    return "```json\n" + json.dumps(a2ui, ensure_ascii=False) + "\n```"


class AgentParseTests(unittest.TestCase):
    def test_extract_prefers_final_full_over_partials(self) -> None:
        raw = "\n".join([
            _sse(_agent_msg("```json\n[par", partial=True)),
            _sse(_agent_msg("tial junk", partial=True)),
            _sse(_agent_msg("FINAL_FULL_TEXT", partial=False)),
        ])
        self.assertEqual(flow_core.extract_agent_text(raw), "FINAL_FULL_TEXT")

    def test_extract_concats_partials_when_no_final(self) -> None:
        raw = "\n".join([
            _sse(_agent_msg("AAA", partial=True)),
            _sse(_agent_msg("BBB", partial=True)),
        ])
        self.assertEqual(flow_core.extract_agent_text(raw), "AAABBB")

    def test_ignores_thinking_and_non_data_lines(self) -> None:
        raw = "\n".join([
            ": comment",
            _sse({"agentMessage": {"agentEvents": [{"thinkingEvent": {"thought": "hmm"}}], "responseId": "x"}}),
            _sse(_agent_msg("ONLY", partial=False)),
        ])
        self.assertEqual(flow_core.extract_agent_text(raw), "ONLY")

    def test_parse_variants(self) -> None:
        raw = _sse(_agent_msg(_fenced(_VARIANTS_A2UI), partial=False))
        out = flow_core.parse_agent_response(raw)
        self.assertIsNone(out["single"])
        self.assertEqual(len(out["variants"]), 3)
        titles = [v["title"] for v in out["variants"]]
        self.assertEqual(titles, ["Реалистичное макро", "Сказочный 3D-стиль", "Классическое масло"])
        self.assertTrue(out["variants"][0]["prompt"].startswith("Крупный план"))
        self.assertEqual(out["variants"][2]["value"], "oil_painting")
        self.assertIn("Выберите одно", out["message"])

    def test_parse_variants_from_streamed_partials(self) -> None:
        # Same document, but split across partial chunks with no final-full repeat.
        full = _fenced(_VARIANTS_A2UI)
        mid = len(full) // 2
        raw = "\n".join([
            _sse(_agent_msg(full[:mid], partial=True)),
            _sse(_agent_msg(full[mid:], partial=True)),
        ])
        out = flow_core.parse_agent_response(raw)
        self.assertEqual(len(out["variants"]), 3)

    def test_parse_single_improved_prompt(self) -> None:
        raw = _sse(_agent_msg(_fenced(_SINGLE_A2UI), partial=False))
        out = flow_core.parse_agent_response(raw)
        self.assertEqual(out["variants"], [])
        self.assertIsNotNone(out["single"])
        self.assertTrue(out["single"].startswith("Кинематографичный макропортрет"))
        self.assertNotIn(">", out["single"])
        self.assertNotIn("**", out["single"])

    def test_garbage_stream_is_safe(self) -> None:
        out = flow_core.parse_agent_response("not an sse stream at all")
        self.assertEqual(out, {"variants": [], "single": None, "message": ""})


if __name__ == "__main__":
    unittest.main()
