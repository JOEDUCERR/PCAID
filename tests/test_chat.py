"""
End-to-end tests for pcaid.chat: the whole program, against the fake Ollama
server, with typed input simulated. The profiler is replaced by a canned
profile so the tests are fast and do not depend on the PC running them.

Run from the project root:
    python -m unittest discover -s tests -v
"""
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from pcaid import chat
from fake_ollama import FakeOllama

# A PC like the first real Windows test machine: selector should choose Qwen3.5 4B.
LAPTOP_PROFILE = {
    "os": {"name": "Test OS", "version": "1", "family": "Windows", "arch": "AMD64"},
    "cpu": {"model": "Test CPU", "physical_cores": 8, "logical_cores": 16},
    "memory": {"total_gb": 15.3, "available_gb": 7.4},
    "gpus": [],
    "disk": {"temp_dir": {"free_gb": 53.0}},
}


def run_chat(fake, args, typed_lines):
    """Run chat.main with simulated typing; return (exit code, printed text)."""
    output = io.StringIO()
    with mock.patch("pcaid.chat.collect_profile", return_value=LAPTOP_PROFILE), \
            mock.patch("builtins.input", side_effect=typed_lines), \
            redirect_stdout(output):
        code = chat.main(args + ["--ollama-url", fake.url])
    return code, output.getvalue()


class ChatTests(unittest.TestCase):
    def start(self, **options):
        fake = FakeOllama(**options).start()
        self.addCleanup(fake.stop)
        return fake

    def test_full_session_with_selector_choosing_the_model(self):
        fake = self.start()
        code, text = run_chat(fake, ["my", "PC", "is", "slow"], ["why?", "/exit"])
        self.assertEqual(code, 0)
        self.assertIn("Qwen3.5 4B (qwen3.5:4b)", text)          # chosen by the selector
        self.assertIn("Hello there", text)                      # streamed answer was printed
        self.assertIn("tokens/s", text)                         # stats line
        requests = fake.chat_requests()
        self.assertEqual(len(requests), 2)                      # the problem, then "why?"
        self.assertEqual(requests[0]["messages"][1]["content"], "my PC is slow")
        self.assertEqual(requests[0]["options"]["num_ctx"], 8192)
        self.assertIs(requests[0]["think"], False)              # thinking switched off by default
        self.assertEqual(len(fake.unload_requests()), 1)        # model unloaded on exit
        self.assertIn("Model unloaded", text)

    def test_the_pc_profile_reaches_the_model_as_system_context(self):
        fake = self.start()
        run_chat(fake, ["slow"], ["/exit"])
        system = fake.chat_requests()[0]["messages"][0]["content"]
        self.assertIn("7.4 GB available", system)

    def test_keep_loaded_skips_the_unload(self):
        fake = self.start()
        run_chat(fake, ["--keep-loaded", "slow"], ["/exit"])
        self.assertEqual(fake.unload_requests(), [])

    def test_model_flag_skips_the_selector(self):
        fake = self.start(models=("tinyllama:latest",))
        code, text = run_chat(fake, ["--model", "tinyllama", "slow"], ["/exit"])
        self.assertEqual(code, 0)
        self.assertIn("chosen with --model", text)

    def test_missing_model_tells_the_user_how_to_download_it(self):
        fake = self.start(models=())
        code, text = run_chat(fake, ["slow"], [])
        self.assertEqual(code, 1)
        self.assertIn("ollama pull qwen3.5:4b", text)
        self.assertEqual(fake.chat_requests(), [])

    def test_problem_is_asked_for_when_not_given(self):
        fake = self.start()
        code, _ = run_chat(fake, [], ["printer will not print", "/exit"])
        self.assertEqual(code, 0)
        self.assertEqual(fake.chat_requests()[0]["messages"][1]["content"], "printer will not print")

    def test_commands_do_not_reach_the_model(self):
        fake = self.start()
        _, text = run_chat(fake, ["slow"], ["/help", "/info", "/reset", "/nonsense", "/exit"])
        self.assertEqual(len(fake.chat_requests()), 1)          # only the opening problem
        self.assertIn("Commands:", text)
        self.assertIn("Context window Ollama is running with: 8192", text)
        self.assertIn("Unknown command /nonsense", text)

    def test_too_long_paste_is_refused_politely(self):
        fake = self.start()
        _, text = run_chat(fake, ["slow"], ["x" * 50000, "/exit"])
        self.assertIn("too long for the model's memory", text)
        self.assertEqual(len(fake.chat_requests()), 1)

    def test_ollama_not_running_is_reported_not_crashed(self):
        fake = FakeOllama().start()
        url_of_stopped_server = fake.url
        fake.stop()
        output = io.StringIO()
        with mock.patch("pcaid.chat.collect_profile", return_value=LAPTOP_PROFILE), redirect_stdout(output):
            code = chat.main(["slow", "--ollama-url", url_of_stopped_server])
        self.assertEqual(code, 1)
        self.assertIn("Is Ollama running", output.getvalue())

    def test_no_model_fits_means_a_clear_message(self):
        weak = dict(LAPTOP_PROFILE, memory={"total_gb": 2.0, "available_gb": 1.0})
        fake = self.start()
        output = io.StringIO()
        with mock.patch("pcaid.chat.collect_profile", return_value=weak), redirect_stdout(output):
            code = chat.main(["slow", "--ollama-url", fake.url])
        self.assertEqual(code, 1)
        self.assertIn("cannot run any of the local models", output.getvalue())


if __name__ == "__main__":
    unittest.main()
