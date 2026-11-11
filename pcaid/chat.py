"""
chat.py - plain-text troubleshooting chat in the terminal (step 3).

What it does, in order:
  1. Profiles this PC (profiler.py) and keeps the summary as permanent context.
  2. Picks a model (selector.py), or uses the one given with --model.
  3. Checks that Ollama is running and has that model.
  4. Chats. The model answers in plain text; it cannot run commands yet.
  5. On exit, unloads the model from memory.

Examples (run from the project root):
    python -m pcaid.chat "my laptop is very slow since yesterday"
    python -m pcaid.chat                       # asks for the problem first
    python -m pcaid.chat --light "no sound"    # force the lightest model
    python -m pcaid.chat --model qwen3.5:2b    # skip the selector

The nicer terminal interface (step 6) will replace the plain input()/print()
loop below. The parts underneath it will stay.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Optional

from .ollama_client import DEFAULT_URL, ChatChunk, ChatStats, OllamaClient, OllamaError
from .profiler import collect_profile, summarize
from .prompts import build_system_prompt
from .selector import format_selection, load_models, select_model
from .session import DEFAULT_NUM_CTX, MessageTooLong, Session

HELP_TEXT = """\
Commands:
  /help     show this help
  /info     show the model, context size and memory use
  /system   show the instructions and PC facts given to the model
  /reset    forget the conversation (keeps the PC facts and the original problem)
  /exit     quit  (Ctrl+C at the prompt also quits; Ctrl+C during an answer stops it)"""


# ---------------------------------------------------------------------------
# Small output helpers
# ---------------------------------------------------------------------------

def _dim(text: str) -> str:
    """Grey text for side information, when the terminal is a real terminal."""
    if sys.stdout.isatty() and "NO_COLOR" not in os.environ:
        return f"\033[2m{text}\033[0m"
    return text


def _format_stats(stats: ChatStats, session: Session) -> str:
    parts = [f"{stats.output_tokens} tokens"]
    if stats.tokens_per_second:
        parts.append(f"{stats.tokens_per_second:.1f} tokens/s")
    if stats.load_seconds >= 0.5:
        parts.append(f"model loaded in {stats.load_seconds:.1f}s")
    parts.append(f"context ~{session.estimated_tokens()}/{session.num_ctx} tokens")
    return "[" + ", ".join(parts) + "]"


# ---------------------------------------------------------------------------
# Setup steps
# ---------------------------------------------------------------------------

def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Troubleshoot this PC with a local AI model.")
    parser.add_argument("issue", nargs="*", help="describe the problem (asked for if left out)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--light", action="store_true", help="use the lightest model")
    group.add_argument("--heavy", action="store_true", help="use the largest model that fits")
    parser.add_argument("--model", metavar="TAG", help="use this Ollama model instead of choosing one")
    parser.add_argument("--ctx", type=int, default=DEFAULT_NUM_CTX, metavar="TOKENS",
                        help=f"context window size (default {DEFAULT_NUM_CTX})")
    parser.add_argument("--think", choices=("off", "on"), default="off",
                        help="let models that support it reason before answering (slower; default off)")
    parser.add_argument("--no-stats", action="store_true", help="hide the speed/context line after each answer")
    parser.add_argument("--keep-loaded", action="store_true", help="do not unload the model on exit")
    parser.add_argument("--ollama-url", default=DEFAULT_URL, help=f"local Ollama address (default {DEFAULT_URL})")
    return parser.parse_args(argv)


def _pick_model(args: argparse.Namespace, profile: dict[str, Any]) -> Optional[str]:
    """Return the Ollama tag to use, or None if no model can run here."""
    if args.model:
        print(f"Model: {args.model} (chosen with --model)")
        return args.model

    models, headroom = load_models()
    preference = "light" if args.light else "heavy" if args.heavy else "auto"
    selection = select_model(profile, models, preference, headroom)
    if selection.model is None:
        print("This PC cannot run any of the local models.")
        print(format_selection(selection))
        return None

    print(f"Model: {selection.model.name} ({selection.model.ollama_tag})")
    for warning in selection.warnings:
        print(f"  warning: {warning}")
    return selection.model.ollama_tag


def _resolve_thinking(client: OllamaClient, tag: str, wanted: str) -> Optional[bool]:
    """Decide what to send as 'think'. None means: do not send it at all."""
    if not client.supports_thinking_toggle(tag):
        if wanted == "on":
            print("Note: this model has no thinking mode that can be switched, so --think on is ignored.")
        return None
    return wanted == "on"


# ---------------------------------------------------------------------------
# The conversation
# ---------------------------------------------------------------------------

def _print_answer(session: Session, text: str, show_stats: bool) -> None:
    try:
        stream = session.ask(text)
    except MessageTooLong as exc:
        print(
            f"That message is too long for the model's memory (about {exc.needed} tokens; "
            f"there is room for about {exc.allowed}). Paste less, for example only the last "
            "30 lines of a log."
        )
        return

    dropped_before = session.dropped_messages
    stats: Optional[ChatStats] = None
    in_thinking = False
    print("\nassistant> ", end="", flush=True)
    try:
        for event in stream:
            if isinstance(event, ChatChunk):
                if event.kind == "thinking":
                    in_thinking = True
                    print(_dim(event.text), end="", flush=True)
                else:
                    if in_thinking:
                        print("\n")  # blank line between the reasoning and the answer
                        in_thinking = False
                    print(event.text, end="", flush=True)
            else:
                stats = event
    except KeyboardInterrupt:
        print("\n[stopped. That question was dropped; ask again or rephrase it.]")
        return
    except OllamaError as exc:
        print(f"\n[error: {exc}]")
        return
    finally:
        stream.close()  # also removes the unanswered question if we stopped early

    print()
    if session.dropped_messages > dropped_before:
        print(_dim("(older messages were dropped to fit the model's memory)"))
    if show_stats and stats is not None:
        print(_dim(_format_stats(stats, session)))


def _show_info(session: Session, client: OllamaClient) -> None:
    try:
        loaded = client.running_context(session.model)
    except OllamaError:
        loaded = None
    if session.think is None:
        thinking = "not controlled (model has no switchable thinking mode)"
    else:
        thinking = "on" if session.think else "off"
    print(f"Model: {session.model}")
    print(f"Context window requested: {session.num_ctx} tokens")
    print(f"Context window Ollama is running with: {loaded if loaded else 'unknown (model not loaded yet)'}")
    print(f"Conversation size (estimate): ~{session.estimated_tokens()} tokens; "
          f"messages dropped so far: {session.dropped_messages}")
    print(f"Thinking: {thinking}")


def _handle_command(command: str, session: Session, client: OllamaClient) -> bool:
    """Run a /command. Returns True if the chat should end."""
    name = command.split()[0].lower()
    if name in ("/exit", "/quit"):
        return True
    if name == "/help":
        print(HELP_TEXT)
    elif name == "/info":
        _show_info(session, client)
    elif name == "/system":
        print(session.system_prompt)
    elif name == "/reset":
        session.reset()
        print("Conversation cleared. The PC facts and your original problem are kept.")
    else:
        print(f"Unknown command {name}. Type /help for the list.")
    return False


def _chat_loop(session: Session, client: OllamaClient, first_message: str, show_stats: bool) -> None:
    print(_dim("(the first answer can take a while: the model has to load)"))
    _print_answer(session, first_message, show_stats)
    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text.startswith("/"):
            if _handle_command(text, session, client):
                return
            continue
        _print_answer(session, text, show_stats)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # never crash on a character the console cannot show
    if os.name == "nt":
        os.system("")  # makes the Windows console understand colour codes

    print("Checking this PC...")
    profile = collect_profile(check_network=False)
    pc_summary = summarize(profile)

    tag = _pick_model(args, profile)
    if tag is None:
        return 1

    try:
        client = OllamaClient(args.ollama_url)
        client.version()
        if not client.has_model(tag):
            print(f"The model {tag} is not downloaded yet. Download it with:\n  ollama pull {tag}\n"
                  "then run this again.")
            return 1
        think = _resolve_thinking(client, tag, args.think)
    except OllamaError as exc:
        print(f"Error: {exc}")
        return 1

    issue = " ".join(args.issue).strip()
    if not issue:
        try:
            issue = input("Describe the problem: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
    if not issue:
        print("No problem described, nothing to do.")
        return 0

    session = Session(client, tag, build_system_prompt(pc_summary, issue),
                      num_ctx=args.ctx, think=think)
    print(_dim("Type /help for commands."))
    try:
        _chat_loop(session, client, issue, show_stats=not args.no_stats)
    finally:
        if not args.keep_loaded:
            try:
                client.unload(tag)
                print("Model unloaded from memory.")
            except OllamaError:
                pass  # nothing useful to do; the model unloads itself after its keep-alive time
    return 0


if __name__ == "__main__":
    sys.exit(main())
