import os
import unittest
from unittest.mock import patch

from proxy import Settings


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.ollama_host, "http://127.0.0.1:11434")
        self.assertEqual(settings.listen_port, 8000)
        self.assertEqual(settings.read_timeout, 21600)
        self.assertEqual(settings.max_request_bytes, 64 * 1024 * 1024)
        self.assertFalse(settings.ollama_think)
        self.assertFalse(settings.debug)

    def test_overrides_and_truthy_values(self):
        with patch.dict(
            os.environ,
            {
                "OLLAMA_HOST": "http://ai-server:11434",
                "LISTEN_HOST": "127.0.0.1",
                "LISTEN_PORT": "9000",
                "CONNECT_TIMEOUT": "4.5",
                "READ_TIMEOUT": "8",
                "STREAM_IDLE_TIMEOUT": "9",
                "MAX_REQUEST_BYTES": "100",
                "OLLAMA_KEEP_ALIVE": "1h",
                "OLLAMA_THINK": "YES",
                "DEBUG": "true",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.ollama_host, "http://ai-server:11434")
        self.assertEqual(settings.listen_port, 9000)
        self.assertEqual(settings.connect_timeout, 4.5)
        self.assertTrue(settings.ollama_think)
        self.assertTrue(settings.debug)

    def test_invalid_number_raises_at_startup(self):
        with patch.dict(os.environ, {"LISTEN_PORT": "bad"}, clear=True):
            with self.assertRaises(ValueError):
                Settings.from_env()


if __name__ == "__main__":
    unittest.main()
