"""
A tiny fake Ollama server for tests. Not part of the product.

It speaks the parts of the Ollama HTTP API that pcaid uses, including the
chunked, line-by-line streaming of /api/chat, so the real client code is
exercised exactly as it would be against Ollama. It records every request so
tests can check what was sent.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional


class FakeOllama:
    def __init__(
        self,
        models=("qwen3.5:4b",),
        reply_chunks=("Hello", " there"),
        thinking_chunks=(),
        capabilities=("completion", "thinking"),
        thinking_meta=None,
        error_after_first_chunk: Optional[str] = None,
        hold_before_done: Optional[threading.Event] = None,
        loaded_context=8192,
    ):
        self.models = list(models)
        self.reply_chunks = list(reply_chunks)
        self.thinking_chunks = list(thinking_chunks)
        self.capabilities = list(capabilities)
        self.thinking_meta = thinking_meta if thinking_meta is not None else {"values": [False, True], "default": True}
        self.error_after_first_chunk = error_after_first_chunk
        self.hold_before_done = hold_before_done
        self.loaded_context = loaded_context
        self.requests = []  # (method, path, json body or None)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        # a short poll interval makes stop() return quickly (the default is half a second)
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.01), daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    def knows(self, model):
        """Real Ollama treats 'name' as 'name:latest'."""
        return model in self.models or (model is not None and ":" not in model and f"{model}:latest" in self.models)

    def chat_requests(self):
        return [body for method, path, body in self.requests if path == "/api/chat"]

    def unload_requests(self):
        return [body for method, path, body in self.requests
                if path == "/api/generate" and body and body.get("keep_alive") == 0]

    def _make_handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # needed for chunked streaming

            def log_message(self, *args):  # keep test output quiet
                pass

            # -- helpers --
            def _body(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                return json.loads(raw) if raw else None

            def _send_json(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _chunk(self, payload):
                data = (json.dumps(payload) + "\n").encode()
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

            def _not_found(self, model):
                self._send_json(404, {"error": f"model '{model}' not found"})

            # -- routes --
            def do_GET(self):
                fake.requests.append(("GET", self.path, None))
                if self.path == "/api/version":
                    self._send_json(200, {"version": "0.0.test"})
                elif self.path == "/api/tags":
                    self._send_json(200, {"models": [{"name": name} for name in fake.models]})
                elif self.path == "/api/ps":
                    self._send_json(200, {"models": [
                        {"name": name, "model": name, "context_length": fake.loaded_context}
                        for name in fake.models]})
                else:
                    self._send_json(404, {"error": "unknown path"})

            def do_POST(self):
                body = self._body()
                fake.requests.append(("POST", self.path, body))
                model = (body or {}).get("model")
                if self.path == "/api/show":
                    if not fake.knows(model):
                        return self._not_found(model)
                    info = {"capabilities": fake.capabilities}
                    if "thinking" in fake.capabilities:
                        info["thinking"] = fake.thinking_meta
                    self._send_json(200, info)
                elif self.path == "/api/generate":
                    self._send_json(200, {"model": model, "done": True})
                elif self.path == "/api/chat":
                    if not fake.knows(model):
                        return self._not_found(model)
                    self._stream_chat(model)
                else:
                    self._send_json(404, {"error": "unknown path"})

            def _stream_chat(self, model):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                try:
                    for piece in fake.thinking_chunks:
                        self._chunk({"model": model, "message": {"role": "assistant", "content": "", "thinking": piece}, "done": False})
                    for index, piece in enumerate(fake.reply_chunks):
                        self._chunk({"model": model, "message": {"role": "assistant", "content": piece}, "done": False})
                        if index == 0 and fake.error_after_first_chunk:
                            self._chunk({"error": fake.error_after_first_chunk})
                            self.wfile.write(b"0\r\n\r\n")
                            return
                    if fake.hold_before_done is not None:
                        fake.hold_before_done.wait(timeout=5)
                    self._chunk({
                        "model": model, "message": {"role": "assistant", "content": ""},
                        "done": True, "done_reason": "stop",
                        "load_duration": 1_000_000_000, "prompt_eval_count": 30,
                        "eval_count": 40, "eval_duration": 2_000_000_000,
                    })
                    self.wfile.write(b"0\r\n\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the client hung up mid-stream, which is exactly what some tests do

        return Handler
