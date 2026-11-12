"""
session.py - one troubleshooting conversation: history, context budget, trimming.

Why trimming exists: the model can only "see" num_ctx tokens at once. If the
conversation grows past that, the oldest text is silently cut off, and the
model may lose its instructions. So we manage it ourselves:

  * The system message (rules + PC facts) is sent every time and is NEVER
    dropped.
  * When the history gets too long, the oldest user+assistant pairs are dropped
    first. The newest message is always kept.
  * The user's first message (the problem) is normally just the first message
    of the history. Once it has been dropped (or the chat was reset), it is
    added to the system message instead, so the model never loses it.
  * A message that cannot fit even on its own is refused with a clear error.

Token counts are estimates (characters / 3, deliberately pessimistic) because
counting exactly would need the model's own tokenizer.
"""
from __future__ import annotations

from typing import Iterator, Optional, Union

from .ollama_client import ChatChunk, ChatStats, OllamaClient
from .prompts import with_original_problem

DEFAULT_NUM_CTX = 8192
CONTEXT_BUDGET_FRACTION = 0.75   # the rest is left free for the model's answer
CHARS_PER_TOKEN = 3.0            # English is about 4, logs and paths are denser
MESSAGE_OVERHEAD_TOKENS = 4      # role markers and separators


class MessageTooLong(Exception):
    def __init__(self, needed: int, allowed: int):
        super().__init__(f"message needs about {needed} tokens, only {allowed} fit")
        self.needed = needed
        self.allowed = allowed


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + MESSAGE_OVERHEAD_TOKENS


def trim_turns(history: list[dict[str, str]], budget_tokens: int) -> list[dict[str, str]]:
    """Drop the oldest user+assistant pairs until the estimate fits the budget.

    `history` alternates user, assistant, user, ... and ends with the newest
    user message. The result keeps that shape, and always keeps the newest
    message, even if it alone is over budget (ask() rejects that case earlier).
    """
    kept = list(history)
    total = sum(estimate_tokens(message["content"]) for message in kept)
    while total > budget_tokens and len(kept) > 1:
        for _ in range(2):  # one user message and the reply to it
            if len(kept) > 1:
                total -= estimate_tokens(kept.pop(0)["content"])
    return kept


class Session:
    def __init__(
        self,
        client: OllamaClient,
        model: str,
        base_prompt: str,
        issue: str,
        num_ctx: int = DEFAULT_NUM_CTX,
        think: Optional[bool] = None,
        keep_alive: str = "30m",
    ):
        self.client = client
        self.model = model
        self.base_prompt = base_prompt   # rules + PC facts, fixed for the session
        self.issue = issue.strip()       # the user's first message
        self.issue_in_prompt = False     # True once the issue is no longer in the history
        self.num_ctx = num_ctx
        self.think = think
        self.keep_alive = keep_alive
        self.history: list[dict[str, str]] = []   # user and assistant messages only
        self.dropped_messages = 0                 # total dropped so far to fit the context

    @property
    def system_prompt(self) -> str:
        """What the model is told before the conversation starts."""
        if self.issue_in_prompt:
            return with_original_problem(self.base_prompt, self.issue)
        return self.base_prompt

    @property
    def history_budget(self) -> int:
        """Tokens available for history, after the system message and answer room.

        Room for the original-problem note is always reserved, so the budget
        does not shrink in the middle of a conversation.
        """
        largest_prompt = with_original_problem(self.base_prompt, self.issue)
        return int(self.num_ctx * CONTEXT_BUDGET_FRACTION) - estimate_tokens(largest_prompt)

    def estimated_tokens(self) -> int:
        return estimate_tokens(self.system_prompt) + sum(
            estimate_tokens(message["content"]) for message in self.history
        )

    def reset(self) -> None:
        """Forget the conversation. The PC facts stay, and the problem moves into the system message."""
        self.history.clear()
        self.issue_in_prompt = True

    def ask(self, user_text: str) -> Iterator[Union[ChatChunk, ChatStats]]:
        """Send a message; returns a stream of the reply.

        Raises MessageTooLong straight away if the message can never fit. The
        conversation only changes once you start reading the stream:
          * on a complete answer, both your message and the reply are kept;
          * if the stream fails or is closed early, your message is removed, as
            if it had never been sent.
        """
        needed = estimate_tokens(user_text)
        if needed > self.history_budget:
            raise MessageTooLong(needed, self.history_budget)
        return self._answer(user_text)

    def _answer(self, user_text: str) -> Iterator[Union[ChatChunk, ChatStats]]:
        self.history.append({"role": "user", "content": user_text})
        kept = trim_turns(self.history, self.history_budget)
        dropped_now = len(self.history) - len(kept)
        self.dropped_messages += dropped_now
        if dropped_now:
            self.issue_in_prompt = True  # the oldest pair held the problem
        self.history = kept

        messages = [{"role": "system", "content": self.system_prompt}] + self.history
        stream = self.client.chat_stream(
            self.model, messages, self.num_ctx, think=self.think, keep_alive=self.keep_alive
        )
        reply_parts: list[str] = []
        completed = False
        try:
            for event in stream:
                if isinstance(event, ChatStats):
                    completed = True
                elif event.kind == "content":
                    reply_parts.append(event.text)
                yield event
        finally:
            stream.close()
            if completed:
                self.history.append({"role": "assistant", "content": "".join(reply_parts)})
            else:
                self.history.pop()  # the unanswered user message
