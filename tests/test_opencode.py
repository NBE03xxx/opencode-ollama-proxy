import json
from pathlib import Path
import unittest

from agents.opencode import (
    build_chat_request,
    chat_completion_from_ollama,
    chat_stream_events,
)


def fixed_id(prefix, length):
    return prefix + ("a" * length)


class OpenCodeTests(unittest.TestCase):
    def test_build_chat_request(self):
        model, stream, request = build_chat_request(
            {
                "model": "qwen",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
                "max_tokens": "42",
                "tools": [{"type": "function", "function": {"name": "f"}}],
                "tool_choice": {"type": "function", "function": {"name": "f"}},
            }
        )
        self.assertEqual(model, "qwen")
        self.assertTrue(stream)
        self.assertEqual(request["messages"], [{"role": "user", "content": "hello"}])
        self.assertEqual(request["options"], {"num_predict": 42})
        self.assertIn("tools", request)
        self.assertIn("tool_choice", request)

    def test_tool_choice_none_omits_tools(self):
        _, _, request = build_chat_request(
            {"model": "m", "messages": [], "tools": [{}], "tool_choice": "none"}
        )
        self.assertNotIn("tools", request)

    def test_non_stream_tool_call(self):
        response = chat_completion_from_ollama(
            {
                "message": {
                    "thinking": "private chain",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "f", "arguments": {"x": 1}}}
                    ],
                },
                "prompt_eval_count": 3,
                "eval_count": 2,
                "done_reason": "stop",
            },
            "m",
            created=100,
            id_factory=fixed_id,
        )
        self.assertEqual(response["id"], "chatcmpl-" + "a" * 16)
        self.assertIsNone(response["choices"][0]["message"]["content"])
        self.assertEqual(response["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(
            response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"],
            '{"x":1}',
        )
        self.assertEqual(response["usage"]["total_tokens"], 5)
        self.assertNotIn("private chain", str(response))

    def test_stream_event_order_and_usage(self):
        fixture = Path(__file__).with_name("fixtures") / "chat_stream.ndjson"
        items = [json.loads(line) for line in fixture.read_text().splitlines()]
        events = list(
            chat_stream_events(
                items,
                "m",
                completion_id="chatcmpl-fixed",
                created=10,
            )
        )
        self.assertEqual(events[0].payload["choices"][0]["delta"], {"role": "assistant"})
        self.assertEqual(events[1].payload["choices"][0]["delta"], {"content": "Hi"})
        self.assertEqual(
            events[2].payload["choices"][0]["delta"]["tool_calls"][0]["id"],
            "call_1",
        )
        self.assertEqual(events[-2].payload["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(events[-2].payload["usage"]["total_tokens"], 7)
        self.assertTrue(events[-1].terminal)


if __name__ == "__main__":
    unittest.main()
