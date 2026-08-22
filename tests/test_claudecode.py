import unittest

from agents.claudecode import (
    AnthropicRequestError,
    anthropic_messages_to_ollama,
    build_messages_request,
    message_from_ollama,
    messages_stream_events,
)


def fixed_id(prefix, length):
    return prefix + "x" * length


class ClaudeCodeTests(unittest.TestCase):
    def test_request_conversion_with_system_tools_and_options(self):
        model, stream, request = build_messages_request(
            {
                "model": "qwen",
                "max_tokens": 200,
                "stream": True,
                "system": [
                    {"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}
                ],
                "messages": [{"role": "user", "content": "hello"}],
                "tools": [
                    {
                        "name": "read_file",
                        "description": "Read a file",
                        "input_schema": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                        },
                    }
                ],
                "tool_choice": {"type": "tool", "name": "read_file"},
                "stop_sequences": ["STOP"],
                "temperature": 0.2,
                "top_p": 0.9,
                "thinking": {"type": "adaptive"},
            }
        )
        self.assertEqual(model, "qwen")
        self.assertTrue(stream)
        self.assertEqual(request["messages"][0], {"role": "system", "content": "system"})
        self.assertEqual(request["options"]["num_predict"], 200)
        self.assertEqual(request["options"]["stop"], ["STOP"])
        self.assertEqual(request["tool_choice"]["function"]["name"], "read_file")
        self.assertNotIn("thinking", request)

    def test_tool_history_and_thinking_are_converted_safely(self):
        messages = anthropic_messages_to_ollama(
            None,
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "secret", "signature": "sig"},
                        {"type": "text", "text": "checking"},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "read_file",
                            "input": {"path": "README.md"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": [{"type": "text", "text": "contents"}],
                        },
                        {"type": "text", "text": "continue"},
                    ],
                },
                {"role": "system", "content": "mid-conversation guidance"},
            ],
        )
        self.assertEqual(messages[0]["content"], "checking")
        self.assertEqual(messages[0]["tool_calls"][0]["id"], "toolu_1")
        self.assertEqual(messages[1]["role"], "tool")
        self.assertEqual(messages[1]["tool_call_id"], "toolu_1")
        self.assertEqual(messages[2], {"role": "user", "content": "continue"})
        self.assertEqual(
            messages[3],
            {"role": "system", "content": "mid-conversation guidance"},
        )
        self.assertNotIn("secret", str(messages))

    def test_non_stream_response_hides_thinking_and_preserves_tool(self):
        response = message_from_ollama(
            {
                "message": {
                    "thinking": "private chain",
                    "content": "I will read it.",
                    "tool_calls": [
                        {
                            "id": "toolu_1",
                            "function": {
                                "name": "read_file",
                                "arguments": {"path": "README.md"},
                            },
                        }
                    ],
                },
                "prompt_eval_count": 10,
                "eval_count": 7,
                "done_reason": "stop",
            },
            "qwen",
            message_id="msg_1",
            id_factory=fixed_id,
        )
        self.assertEqual(response["stop_reason"], "tool_use")
        self.assertEqual(response["content"][0]["text"], "I will read it.")
        self.assertEqual(response["content"][1]["type"], "tool_use")
        self.assertNotIn("private chain", str(response))
        self.assertEqual(response["usage"], {"input_tokens": 10, "output_tokens": 7})

    def test_empty_visible_response_becomes_safe_diagnostic(self):
        response = message_from_ollama(
            {
                "message": {"thinking": "private chain"},
                "eval_count": 20,
                "done_reason": "length",
            },
            "qwen",
            message_id="msg_1",
        )
        self.assertEqual(response["stop_reason"], "max_tokens")
        self.assertIn("no visible text", response["content"][0]["text"])
        self.assertNotIn("private chain", str(response))

        events = list(
            messages_stream_events(
                [
                    {"message": {"thinking": "private chain"}},
                    {
                        "message": {},
                        "done": True,
                        "eval_count": 20,
                        "done_reason": "length",
                    },
                ],
                "qwen",
                message_id="msg_1",
            )
        )
        rendered = str([event.payload for event in events])
        self.assertIn("no visible text", rendered)
        self.assertNotIn("private chain", rendered)
        self.assertEqual(events[-2].payload["delta"]["stop_reason"], "max_tokens")

    def test_stream_sequence_ping_tool_and_thinking_hidden(self):
        events = list(
            messages_stream_events(
                [
                    None,
                    {"message": {"thinking": "secret"}},
                    {"message": {"content": "hello"}},
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "toolu_1",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": {"path": "README.md"},
                                    },
                                }
                            ]
                        }
                    },
                    {
                        "message": {},
                        "done": True,
                        "prompt_eval_count": 10,
                        "eval_count": 7,
                        "done_reason": "stop",
                    },
                ],
                "qwen",
                message_id="msg_1",
                id_factory=fixed_id,
            )
        )
        names = [event.event for event in events]
        self.assertEqual(names[0], "message_start")
        self.assertIn("ping", names)
        self.assertEqual(names[-2:], ["message_delta", "message_stop"])
        self.assertEqual(events[-2].payload["delta"]["stop_reason"], "tool_use")
        self.assertEqual(
            events[-2].payload["usage"],
            {"input_tokens": 10, "output_tokens": 7},
        )
        rendered = str([event.payload for event in events])
        self.assertIn("hello", rendered)
        self.assertIn("read_file", rendered)
        self.assertNotIn("secret", rendered)
        self.assertNotIn("thinking_delta", rendered)

    def test_stream_requires_done_marker(self):
        with self.assertRaises(RuntimeError):
            list(messages_stream_events([], "qwen", id_factory=fixed_id))

    def test_invalid_request(self):
        with self.assertRaises(AnthropicRequestError):
            build_messages_request({"model": "qwen", "messages": []})
        with self.assertRaises(AnthropicRequestError):
            build_messages_request(
                {"model": "qwen", "max_tokens": 1, "messages": "bad"}
            )
        with self.assertRaises(AnthropicRequestError):
            build_messages_request(
                {"model": "qwen", "max_tokens": 1.5, "messages": []}
            )
        with self.assertRaises(AnthropicRequestError):
            build_messages_request(
                {"model": "qwen", "max_tokens": 0, "messages": []}
            )
        with self.assertRaises(AnthropicRequestError):
            build_messages_request(
                {
                    "model": "qwen",
                    "max_tokens": 1,
                    "messages": [],
                    "stream": "true",
                }
            )


if __name__ == "__main__":
    unittest.main()
