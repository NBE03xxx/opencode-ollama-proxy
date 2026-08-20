import json
from pathlib import Path
import unittest

from agents.codex import (
    build_responses_request,
    responses_from_ollama,
    responses_input_to_messages,
    responses_stream_events,
    responses_tools_to_ollama,
)


def fixed_id(prefix, length):
    return prefix + ("b" * length)


class CodexTests(unittest.TestCase):
    def test_input_history_conversion(self):
        messages = responses_input_to_messages(
            "base",
            [
                {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "dev"}]},
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "run"}]},
                {"type": "function_call", "call_id": "call_1", "name": "f", "arguments": {"x": 1}},
                {"type": "function_call_output", "call_id": "call_1", "output": {"ok": True}},
            ],
            id_factory=fixed_id,
        )
        self.assertEqual(messages[0], {"role": "system", "content": "base\n\ndev"})
        self.assertEqual(messages[1], {"role": "user", "content": "run"})
        self.assertEqual(messages[2]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(messages[3]["role"], "tool")

    def test_flat_tools_conversion(self):
        converted = responses_tools_to_ollama(
            [{"type": "function", "name": "f", "description": "d", "parameters": {"type": "object"}}]
        )
        self.assertEqual(converted[0]["function"]["name"], "f")
        self.assertEqual(converted[0]["function"]["description"], "d")

    def test_build_responses_request(self):
        model, stream, request = build_responses_request(
            {
                "model": "m",
                "stream": True,
                "input": "hello",
                "max_output_tokens": 12,
                "tools": [{"type": "function", "name": "f"}],
                "tool_choice": {"type": "function", "name": "f"},
            }
        )
        self.assertEqual(model, "m")
        self.assertTrue(stream)
        self.assertEqual(request["options"], {"num_predict": 12})
        self.assertEqual(request["tool_choice"]["function"]["name"], "f")

    def test_non_stream_response(self):
        response = responses_from_ollama(
            {
                "message": {
                    "content": "answer",
                    "tool_calls": [
                        {"id": "call_1", "function": {"name": "f", "arguments": {"x": 1}}}
                    ],
                },
                "prompt_eval_count": 2,
                "eval_count": 3,
            },
            "m",
            created=50,
            id_factory=fixed_id,
        )
        self.assertEqual([item["type"] for item in response["output"]], ["message", "function_call"])
        self.assertEqual(response["output"][1]["call_id"], "call_1")
        self.assertEqual(response["usage"]["total_tokens"], 5)

    def test_stream_sequence_and_final_output(self):
        fixture = Path(__file__).with_name("fixtures") / "responses_stream.ndjson"
        items = [json.loads(line) for line in fixture.read_text().splitlines()]
        events = list(
            responses_stream_events(
                items,
                "m",
                response_id="resp_fixed",
                created=10,
                id_factory=fixed_id,
            )
        )
        payloads = [event.payload for event in events if event.payload]
        self.assertEqual(
            [payload["sequence_number"] for payload in payloads],
            list(range(len(payloads))),
        )
        self.assertEqual(payloads[0]["type"], "response.created")
        self.assertEqual(payloads[-1]["type"], "response.completed")
        final = payloads[-1]["response"]
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["output"][0]["content"][0]["text"], "AB")
        self.assertEqual(final["output"][1]["call_id"], "call_1")
        self.assertEqual(final["usage"]["total_tokens"], 6)
        self.assertTrue(events[-1].terminal)

    def test_stream_without_done_fails(self):
        payloads = [
            event.payload
            for event in responses_stream_events([], "m", created=1, id_factory=fixed_id)
            if event.payload
        ]
        self.assertEqual(payloads[-1]["type"], "response.failed")
        self.assertIn("done marker", payloads[-1]["response"]["error"]["message"])


if __name__ == "__main__":
    unittest.main()
