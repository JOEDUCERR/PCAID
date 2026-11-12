"""
ollama_client.py - minimal client for the Ollama server on this PC.

Standard library only (see PROJECT.md). It implements just the calls this
project needs: version, list models, show model info, streaming chat,
unload, and "which context size is loaded".

Privacy rules enforced here:
  * The client refuses any server address that is not on this PC.
  * It ignores the system proxy settings, so even on a PC configured to send
    web traffic through a proxy, requests to Ollama never leave the machine.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator, Optional, Union
from urllib.parse import urlparse

DEFAULT_URL = "http://127.0.0.1:11434"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


class OllamaError(Exception):
    """A problem talking to Ollama. The message is written to be shown to the user."""


@dataclass(frozen=True)
class ChatChunk:
    """A piece of the reply, delivered as it is generated."""
    kind: str   # "content" (the answer) or "thinking" (reasoning, only when thinking is on)
    text: str


@dataclass(frozen=True)
class ChatStats:
    """The last event of a reply: Ollama's own timing numbers."""
    prompt_tokens: int
    output_tokens: int
    load_seconds: float
    generation_seconds: float

    @property
    def tokens_per_second(self) -> Optional[float]:
        if self.generation_seconds <= 0:
            return None
        return self.output_tokens / self.generation_seconds


def _error_text(exc: urllib.error.HTTPError) -> str:
    """Ollama explains errors as {"error": "..."}; fall back to the HTTP code."""
    try:
        message = json.loads(exc.read().decode("utf-8", errors="replace")).get("error")
    except (ValueError, AttributeError, OSError):
        message = None
    return str(message) if message else f"Ollama returned HTTP {exc.code}."


def _stats_from(event: dict[str, Any]) -> ChatStats:
    nanoseconds = 1e9  # Ollama reports durations in nanoseconds
    return ChatStats(
        prompt_tokens=int(event.get("prompt_eval_count") or 0),
        output_tokens=int(event.get("eval_count") or 0),
        load_seconds=(event.get("load_duration") or 0) / nanoseconds,
        generation_seconds=(event.get("eval_duration") or 0) / nanoseconds,
    )


class OllamaClient:
    def __init__(self, base_url: str = DEFAULT_URL, allow_remote: bool = False, timeout: float = 300.0):
        host = urlparse(base_url).hostname
        if host not in LOCAL_HOSTS and not allow_remote:
            raise OllamaError(
                f"{base_url!r} is not a local Ollama address. This tool only talks to "
                f"Ollama on this PC (use a full address such as {DEFAULT_URL})."
            )
        self.base_url = base_url.rstrip("/")
        # Generous: the first reply has to wait for the model to load, which
        # can take a while on a slow PC.
        self.timeout = timeout
        # An empty ProxyHandler means "no proxy, whatever the system says".
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # -- plumbing -----------------------------------------------------------

    def _open(self, method: str, path: str, payload: Optional[dict] = None,
              timeout: Optional[float] = None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            return self._opener.open(request, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:  # must come before URLError: it is a subclass
            raise OllamaError(_error_text(exc)) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise OllamaError(
                f"Cannot reach Ollama at {self.base_url} ({exc}). "
                "Is Ollama running? Start the Ollama app, or run 'ollama serve'."
            ) from exc

    @staticmethod
    def _read_json(response) -> dict[str, Any]:
        try:
            data = json.load(response)
        except (ValueError, OSError) as exc:
            raise OllamaError("Ollama sent a reply this program cannot read.") from exc
        if not isinstance(data, dict):
            raise OllamaError("Ollama sent a reply this program cannot read.")
        return data

    def _get_json(self, path: str) -> dict[str, Any]:
        with self._open("GET", path, timeout=10.0) as response:
            return self._read_json(response)

    # -- simple calls -------------------------------------------------------

    def version(self) -> str:
        return str(self._get_json("/api/version").get("version", "unknown"))

    def list_models(self) -> list[str]:
        """Names of the models downloaded on this PC."""
        models = self._get_json("/api/tags").get("models") or []
        return [str(entry.get("name")) for entry in models]

    def has_model(self, tag: str) -> bool:
        # 'qwen3' and 'qwen3:latest' are the same model.
        wanted = {tag} if ":" in tag else {tag, tag + ":latest"}
        return any(name in wanted for name in self.list_models())

    def show(self, model: str) -> dict[str, Any]:
        with self._open("POST", "/api/show", {"model": model}, timeout=30.0) as response:
            return self._read_json(response)

    def supports_thinking_toggle(self, model: str) -> bool:
        """True if the model has a thinking mode that can be switched off.

        Only then is it safe (and useful) to send an explicit think=False.
        """
        info = self.show(model)
        if "thinking" not in (info.get("capabilities") or []):
            return False
        values = (info.get("thinking") or {}).get("values")
        return values is None or False in values

    def running_context(self, model: str) -> Optional[int]:
        """Context size (tokens) the loaded model is actually running with."""
        for entry in self._get_json("/api/ps").get("models") or []:
            if model in (entry.get("name"), entry.get("model")):
                return entry.get("context_length")
        return None

    def preload(self, model: str, num_ctx: int, keep_alive: str = "30m") -> None:
        """Load the model into memory now, so the first real question is not slow.

        Ollama's documented way to do this is a chat request with no messages.
        On a slow disk this call can take a minute or more.
        """
        payload = {
            "model": model,
            "messages": [],
            "stream": False,
            "options": {"num_ctx": num_ctx},
            "keep_alive": keep_alive,
        }
        with self._open("POST", "/api/chat", payload) as response:
            self._read_json(response)

    def unload(self, model: str) -> None:
        """Free the model's memory now. (The downloaded files stay on disk.)"""
        payload = {"model": model, "keep_alive": 0}
        with self._open("POST", "/api/generate", payload, timeout=30.0) as response:
            response.read()

    # -- chat ---------------------------------------------------------------

    def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        num_ctx: int,
        think: Optional[bool] = None,
        keep_alive: str = "30m",
    ) -> Iterator[Union[ChatChunk, ChatStats]]:
        """Send the conversation and yield the reply piece by piece.

        Yields ChatChunk items, then one final ChatStats. Always close the
        generator (stream.close()) if you stop reading early: that closes the
        connection, which tells Ollama to stop generating.

        num_ctx is always sent explicitly, because Ollama's default context
        window is small and silently cuts off long conversations.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"num_ctx": num_ctx},
            "keep_alive": keep_alive,
        }
        if think is not None:
            payload["think"] = think

        response = self._open("POST", "/api/chat", payload)
        try:
            for raw_line in response:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError as exc:
                    raise OllamaError("Ollama sent a reply this program cannot read.") from exc
                if "error" in event:
                    raise OllamaError(str(event["error"]))

                message = event.get("message") or {}
                if message.get("thinking"):
                    yield ChatChunk("thinking", message["thinking"])
                if message.get("content"):
                    yield ChatChunk("content", message["content"])
                if event.get("done"):
                    yield _stats_from(event)
                    return
            raise OllamaError("Ollama stopped before the answer was finished.")
        except OSError as exc:  # connection dropped or timed out mid-answer
            raise OllamaError(f"Lost the connection to Ollama while it was answering ({exc}).") from exc
        finally:
            response.close()
