"""
Tests for pcaid.ollama_client, run against the fake Ollama server.

Run from the project root:
    python -m unittest discover -s tests -v
"""
import os
import socket
import threading
import unittest
from unittest import mock

from pcaid.ollama_client import ChatChunk, ChatStats, OllamaClient, OllamaError
from fake_ollama import FakeOllama

MESSAGES = [{"role": "user", "content": "hi"}]


def read_all(stream):
    return list(stream)


class ClientTests(unittest.TestCase):
    def start(self, **options):
        fake = FakeOllama(**options).start()
        self.addCleanup(fake.stop)
        return fake, OllamaClient(fake.url)

    # -- simple calls --
    def test_version(self):
        _, client = self.start()
        self.assertEqual(client.version(), "0.0.test")

    def test_has_model_handles_latest_suffix(self):
        _, client = self.start(models=("qwen3.5:4b", "tinyllama:latest"))
        self.assertTrue(client.has_model("qwen3.5:4b"))
        self.assertTrue(client.has_model("tinyllama"))
        self.assertFalse(client.has_model("qwen3.5:9b"))

    def test_running_context(self):
        _, client = self.start(loaded_context=4096)
        self.assertEqual(client.running_context("qwen3.5:4b"), 4096)
        self.assertIsNone(client.running_context("other:model"))

    def test_unload_sends_keep_alive_zero(self):
        fake, client = self.start()
        client.unload("qwen3.5:4b")
        self.assertEqual(fake.unload_requests(), [{"model": "qwen3.5:4b", "keep_alive": 0}])

    def test_preload_sends_a_chat_request_with_no_messages(self):
        fake, client = self.start()
        client.preload("qwen3.5:4b", num_ctx=6000, keep_alive="10m")
        self.assertEqual(fake.preload_requests(), [{
            "model": "qwen3.5:4b", "messages": [], "stream": False,
            "options": {"num_ctx": 6000}, "keep_alive": "10m"}])
        self.assertEqual(fake.chat_requests(), [])  # it is not counted as a question

    def test_preload_reports_loading_errors(self):
        _, client = self.start(preload_error="not enough memory to load model")
        with self.assertRaisesRegex(OllamaError, "not enough memory"):
            client.preload("qwen3.5:4b", num_ctx=8192)

    # -- thinking support --
    def test_thinking_toggle_supported(self):
        _, client = self.start()
        self.assertTrue(client.supports_thinking_toggle("qwen3.5:4b"))

    def test_no_thinking_capability_means_no_toggle(self):
        _, client = self.start(capabilities=("completion",))
        self.assertFalse(client.supports_thinking_toggle("qwen3.5:4b"))

    def test_thinking_levels_only_means_no_toggle(self):
        _, client = self.start(thinking_meta={"values": ["low", "high"], "default": "low"})
        self.assertFalse(client.supports_thinking_toggle("qwen3.5:4b"))

    # -- chat streaming --
    def test_chat_stream_yields_text_then_stats(self):
        _, client = self.start()
        events = read_all(client.chat_stream("qwen3.5:4b", MESSAGES, num_ctx=8192))
        self.assertEqual([e.text for e in events if isinstance(e, ChatChunk)], ["Hello", " there"])
        stats = events[-1]
        self.assertIsInstance(stats, ChatStats)
        self.assertEqual(stats.output_tokens, 40)
        self.assertAlmostEqual(stats.tokens_per_second, 20.0)
        self.assertAlmostEqual(stats.load_seconds, 1.0)

    def test_chat_request_always_sets_context_size(self):
        fake, client = self.start()
        read_all(client.chat_stream("qwen3.5:4b", MESSAGES, num_ctx=6000, keep_alive="10m"))
        body = fake.chat_requests()[0]
        self.assertEqual(body["options"], {"num_ctx": 6000})
        self.assertEqual(body["keep_alive"], "10m")
        self.assertTrue(body["stream"])

    def test_think_is_only_sent_when_set(self):
        fake, client = self.start()
        read_all(client.chat_stream("qwen3.5:4b", MESSAGES, num_ctx=1000))
        read_all(client.chat_stream("qwen3.5:4b", MESSAGES, num_ctx=1000, think=False))
        first, second = fake.chat_requests()
        self.assertNotIn("think", first)
        self.assertIs(second["think"], False)

    def test_thinking_text_is_labelled(self):
        _, client = self.start(thinking_chunks=("hmm",))
        chunks = [e for e in read_all(client.chat_stream("qwen3.5:4b", MESSAGES, 1000)) if isinstance(e, ChatChunk)]
        self.assertEqual([(c.kind, c.text) for c in chunks],
                         [("thinking", "hmm"), ("content", "Hello"), ("content", " there")])

    def test_text_arrives_before_the_reply_is_finished(self):
        release = threading.Event()
        self.addCleanup(release.set)
        _, client = self.start(hold_before_done=release)
        stream = client.chat_stream("qwen3.5:4b", MESSAGES, 1000)
        first = next(stream)  # would block forever if the client waited for the whole reply
        self.assertEqual(first.text, "Hello")
        release.set()
        stream.close()

    # -- errors --
    def test_unknown_model_gives_ollamas_message(self):
        _, client = self.start()
        with self.assertRaisesRegex(OllamaError, "not found"):
            read_all(client.chat_stream("ghost:1b", MESSAGES, 1000))

    def test_error_in_the_middle_of_a_stream(self):
        _, client = self.start(error_after_first_chunk="model runner crashed")
        with self.assertRaisesRegex(OllamaError, "model runner crashed"):
            read_all(client.chat_stream("qwen3.5:4b", MESSAGES, 1000))

    def test_server_not_running(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()  # nothing is listening on this port any more
        client = OllamaClient(f"http://127.0.0.1:{port}")
        with self.assertRaisesRegex(OllamaError, "Is Ollama running"):
            client.version()

    # -- privacy guards --
    def test_refuses_addresses_that_are_not_this_pc(self):
        with self.assertRaises(OllamaError):
            OllamaClient("http://example.com:11434")
        with self.assertRaises(OllamaError):
            OllamaClient("127.0.0.1:11434")  # no scheme: cannot tell the host
        OllamaClient("http://example.com:11434", allow_remote=True)  # explicit opt-in works

    def test_system_proxy_settings_are_ignored(self):
        fake, _ = self.start()
        dead_proxy = {"HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9"}
        with mock.patch.dict(os.environ, dead_proxy):
            client = OllamaClient(fake.url)  # built while the proxy variables are set
            self.assertEqual(client.version(), "0.0.test")


if __name__ == "__main__":
    unittest.main()
