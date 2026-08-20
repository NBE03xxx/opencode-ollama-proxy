import unittest

from common import (
    convert_message_to_ollama,
    json_bytes,
    normalize_content,
    normalize_tool_arguments,
    openai_error,
)


class CommonTests(unittest.TestCase):
    def test_json_bytes_is_compact_unicode(self):
        self.assertEqual(json_bytes({"text": "日本語"}), b'{"text":"\xe6\x97\xa5\xe6\x9c\xac\xe8\xaa\x9e"}')

    def test_openai_error_shape(self):
        self.assertEqual(
            openai_error("bad", "invalid_request_error"),
            {
                "error": {
                    "message": "bad",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": None,
                }
            },
        )

    def test_normalize_content(self):
        self.assertEqual(normalize_content(None), "")
        self.assertEqual(normalize_content("hello"), "hello")
        self.assertEqual(
            normalize_content(
                [
                    {"type": "text", "text": "a"},
                    {"type": "image_url", "url": "ignored"},
                    {"type": "text", "text": "b"},
                    "ignored",
                ]
            ),
            "ab",
        )
        self.assertEqual(normalize_content(12), "12")

    def test_normalize_tool_arguments(self):
        self.assertEqual(normalize_tool_arguments('{"x":1}'), '{"x":1}')
        self.assertEqual(normalize_tool_arguments({"x": "日本"}), '{"x":"日本"}')
        self.assertEqual(normalize_tool_arguments(None), "{}")
        self.assertEqual(normalize_tool_arguments({"x": {1}}), "{}")

    def test_convert_message_preserves_tool_metadata(self):
        converted = convert_message_to_ollama(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "index": 2,
                        "function": {"name": "weather", "arguments": '{"city":"Tokyo"}'},
                    }
                ],
            }
        )
        self.assertEqual(converted["content"], "")
        self.assertEqual(converted["tool_calls"][0]["id"], "call_1")
        self.assertEqual(converted["tool_calls"][0]["index"], 2)
        self.assertEqual(
            converted["tool_calls"][0]["function"]["arguments"],
            {"city": "Tokyo"},
        )

    def test_convert_tool_result(self):
        self.assertEqual(
            convert_message_to_ollama(
                {
                    "role": "tool",
                    "content": "sunny",
                    "tool_call_id": "call_1",
                    "name": "weather",
                }
            ),
            {
                "role": "tool",
                "content": "sunny",
                "tool_call_id": "call_1",
                "name": "weather",
            },
        )


if __name__ == "__main__":
    unittest.main()
