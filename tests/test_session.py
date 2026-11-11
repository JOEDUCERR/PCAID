"""
Tests for pcaid.session and pcaid.prompts, using the fake Ollama server.

Run from the project root:
    python -m unittest discover -s tests -v
"""
import unittest

from pcaid.ollama_client import ChatStats, OllamaClient, OllamaError
from pcaid.prompts import build_system_prompt
from pcaid.session import MessageTooLong, Session, estimate_tokens, trim_turns
from fake_ollama import FakeOllama

MODEL = "qwen3.5:4b"


def turn_list(*texts):
    """Alternating user/assistant messages starting with a user message."""
    roles = ["user", "assistant"]
    return [{"role": roles[i % 2], "content": text} for i, text in enumerate(texts)]


class PromptTests(unittest.TestCase):
    def test_prompt_contains_pc_facts_and_problem(self):
        prompt = build_system_prompt("CPU: Test 9000\n", "  no sound after update \n")
        self.assertIn("CPU: Test 9000", prompt)
        self.assertIn("no sound after update", prompt)
        self.assertIn("2 to 4 numbered options", prompt)

    def test_braces_in_the_problem_text_are_safe(self):
        prompt = build_system_prompt("OS: x", "error {0} and {name} in log")
        self.assertIn("error {0} and {name} in log", prompt)


class TrimTests(unittest.TestCase):
    def test_nothing_dropped_when_it_fits(self):
        history = turn_list("a" * 30, "b" * 30, "c" * 30)
        self.assertEqual(trim_turns(history, budget_tokens=1000), history)

    def test_oldest_pair_goes_first_and_newest_is_kept(self):
        history = turn_list("a" * 300, "b" * 300, "c" * 300, "d" * 300, "e" * 30)
        kept = trim_turns(history, budget_tokens=400)
        self.assertEqual(kept[-1]["content"], "e" * 30)
        self.assertEqual(kept[0]["role"], "user")             # shape stays user, assistant, user...
        self.assertEqual(len(kept) % 2, 1)
        self.assertLess(len(kept), len(history))
        self.assertEqual(kept, history[len(history) - len(kept):])  # only the oldest were removed

    def test_newest_message_survives_even_if_over_budget(self):
        kept = trim_turns(turn_list("a" * 30, "b" * 30, "c" * 3000), budget_tokens=100)
        self.assertEqual(kept, [{"role": "user", "content": "c" * 3000}])


class SessionTests(unittest.TestCase):
    def start(self, num_ctx=8192, **options):
        fake = FakeOllama(**options).start()
        self.addCleanup(fake.stop)
        client = OllamaClient(fake.url)
        session = Session(client, MODEL, build_system_prompt("OS: TestOS", "PC is slow"), num_ctx=num_ctx)
        return fake, session

    def test_conversation_is_remembered_and_system_prompt_comes_first(self):
        fake, session = self.start()
        list(session.ask("first question"))
        list(session.ask("second question"))
        self.assertEqual([m["role"] for m in session.history], ["user", "assistant", "user", "assistant"])
        self.assertEqual(session.history[1]["content"], "Hello there")

        second_request = fake.chat_requests()[1]["messages"]
        self.assertEqual(second_request[0]["role"], "system")
        self.assertIn("PC is slow", second_request[0]["content"])
        self.assertEqual([m["content"] for m in second_request[1:]],
                         ["first question", "Hello there", "second question"])

    def test_stopping_early_removes_the_unanswered_question(self):
        _, session = self.start()
        stream = session.ask("a question I will abandon")
        next(stream)       # first piece of the answer arrives...
        stream.close()     # ...and the user presses Ctrl+C
        self.assertEqual(session.history, [])

    def test_failed_request_removes_the_question(self):
        _, session = self.start()
        session.model = "ghost:1b"
        with self.assertRaises(OllamaError):
            list(session.ask("hello?"))
        self.assertEqual(session.history, [])

    def test_stream_ends_with_stats_and_keeps_the_exchange(self):
        _, session = self.start()
        events = list(session.ask("hi"))
        self.assertIsInstance(events[-1], ChatStats)
        self.assertEqual(len(session.history), 2)

    def test_message_too_big_for_the_context_is_refused(self):
        fake, session = self.start(num_ctx=2048)
        with self.assertRaises(MessageTooLong) as caught:
            session.ask("x" * 20000)
        self.assertGreater(caught.exception.needed, caught.exception.allowed)
        self.assertEqual(session.history, [])
        self.assertEqual(fake.chat_requests(), [])  # nothing was sent

    def test_old_messages_are_dropped_but_the_system_prompt_never_is(self):
        fake, session = self.start(num_ctx=1500)
        for number in range(8):
            list(session.ask(f"question {number} " + "detail " * 60))
        self.assertGreater(session.dropped_messages, 0)
        for request in fake.chat_requests():
            self.assertEqual(request["messages"][0]["role"], "system")
            self.assertIn("PC is slow", request["messages"][0]["content"])
        # What was sent never exceeds the budget, by our own estimate.
        last = fake.chat_requests()[-1]["messages"]
        self.assertLessEqual(sum(estimate_tokens(m["content"]) for m in last), int(1500 * 0.75))

    def test_reset_keeps_the_problem_but_clears_history(self):
        _, session = self.start()
        list(session.ask("hi"))
        session.reset()
        self.assertEqual(session.history, [])
        self.assertIn("PC is slow", session.system_prompt)


if __name__ == "__main__":
    unittest.main()
