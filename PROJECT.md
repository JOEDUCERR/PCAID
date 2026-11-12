# PROJECT.md: pcaid (working name)

_Last updated: 2026-11-11_

> Update rule: this file is updated at the end of **every** completed piece of work
> (new file, change, test result, decision). Newest log entry goes first.
> Step markers: `[x]` done and checked, `[~]` written and tested with fakes but waiting for a
> real-world check, `[ ]` not started.

## 1. What this is

A terminal tool for troubleshooting a PC with a single command, for example
`<command> "<issue explained>"`. It pulls a local AI model onto the client's PC,
diagnoses the issue (and fixes it where safe), asks the user to choose from options
when it needs more information, and removes the model once the issue is solved.
Everything runs locally for client privacy. It is intended to replace physical
desktop-support visits for problems a standard user could not fix without a lot of
IT experience.

## 2. Requirements (from the owner)

- Runs on Windows, macOS and at least Ubuntu.
- Fully local: the model runs on the client PC.
- Single-command start, entirely in the terminal, opencode / Claude Code style UI.
- If the model needs more info, it presents a set of options to the user.
- Ephemeral: the model is removed as soon as the issue is solved.
- Prototype offers 3-4 models, chosen by hardware and issue, with a user override
  for an extremely light or a heavier model.
- On start, run commands to check hardware and software and keep the result as
  permanent context for the session.
- Strictly for PC troubleshooting.

## 3. Decisions

| Decision | Status |
|---|---|
| Build starts with the hardware/software profiler, then the model selector, then the chat loop | Done |
| Keep this PROJECT.md, updated after every piece of work | Agreed |
| Code is built and delivered file by file, reviewed and integrated manually | Agreed |
| Test machines: a Windows 11 laptop and an Ubuntu desktop | Agreed |
| Hardware limits are hard: a user preference never pushes the choice past what fits | Done (selector) |
| `auto` = largest comfortable model; `--light` = smallest; `--heavy` = largest that fits, may be slow | Done (selector) |
| Model tiers live in `models.json`, not in code | Done |
| Four tiers from one model family (Qwen3.5 0.8B / 2B / 4B / 9B) so tool-call format is identical | Proposed (verify on real PCs) |
| Selection ignores the issue text for now; the issue-based nudge waits until the agent loop exists | Proposed |
| Only NVIDIA GPUs and Apple Silicon count as acceleration; AMD/Intel ignored until a runtime that supports them is chosen | Proposed |
| Standard library only so far (profiler, selector, Ollama client, session, chat) | Done |
| No hostname, username, serials, IP/MAC or file paths collected | Done |
| Ollama client talks only to this PC (refuses other addresses) and ignores system proxy settings | Done |
| Context window is always set explicitly (`num_ctx`); default 8192 tokens | Done; value Proposed (tune) |
| System message = rules + the PC's operating system and shell + PC facts; sent every time, never trimmed | Done |
| The user's problem is the first user message; it is added to the system message only once it has been trimmed away or the chat was reset | Done (new, see log 2026-11-11) |
| When history is too long, oldest user+assistant pairs are dropped first; budget is 75% of `num_ctx` | Done |
| Token counts are estimates (characters / 3, pessimistic) | Done; revisit if it misjudges |
| Prompt states the OS and shell outright and forbids other systems' tools; asks for plain text (no Markdown) | Done (new), to be checked on real models |
| The model is loaded up front with a visible "Loading..." message, before the first question | Done (new) |
| Thinking mode is switched off by default when the model allows it (checked via `/api/show`); `--think on` enables it | Proposed (verify speed and quality) |
| Chat is a plain `input()`/`print()` loop; the real terminal UI is step 6 | Agreed |
| Ctrl+C during an answer stops it and drops that question; Ctrl+C at the prompt quits | Done |
| On exit the model is unloaded from memory (files stay on disk until step 7) | Done |
| `--model TAG` bypasses the selector (for comparing models and tuning thresholds) | Done |
| The model gets a toolbox of named, tested functions, not a raw shell | Proposed (step 4) |
| Small tiers (0.8B, 2B) only choose from the toolbox and numbered options; they do not write free-form commands | Proposed (step 4), based on the 2B run |
| Three permission tiers: read-only auto-runs; changes need a yes; destructive actions blocked | Proposed (step 5) |
| Weak-PC fallback ladder: no-AI runbooks, tiny model, remote model on LAN, offline model from USB | Proposed |
| Python 3.9+ for the prototype | Proposed |
| Prototype on Ollama behind a runtime interface; later swap to llama.cpp in a temp folder for easy cleanup | Proposed |

## 4. Build plan

- [x] 1. Profiler: hardware/software facts as compact JSON + short text summary
- [x] 2. Model selector: pure function (profile + preference -> model or none), config-driven
- [~] 3. Chat loop: ran on real Ollama (Ubuntu desktop, 2B and 4B); fixes from that run (section 9) not yet re-checked
- [ ] 4. Read-only tools + tool-calling loop (the system prompt's "cannot run anything yet" changes here)
- [ ] 5. Permission layer + command log (+ restore point before changes)
- [ ] 6. Terminal UI: streaming output, option menus
- [ ] 7. Lifecycle: download, crash-safe cleanup, `--keep` flag, orphan sweep on next run
- [ ] 8. Packaging, one-line installers, code signing, testing in VMs and on a real Mac
- [ ] 9. Evaluation set of 20-30 realistic issues

## 5. Layout

```
PROJECT.md
pcaid/
  __init__.py
  profiler.py        # step 1: probe the PC
  selector.py        # step 2: choose a model from the profile
  models.json        # step 2: model tiers and thresholds (edit here to change models)
  ollama_client.py   # step 3: talk to the local Ollama server (chat, preload, unload, ...)
  prompts.py         # step 3: the system prompt
  session.py         # step 3: history, context budget, trimming
  chat.py            # step 3: the terminal chat program
tests/
  fake_ollama.py     # fake Ollama server used by the tests (not part of the product)
  test_profiler.py, test_selector.py, test_ollama_client.py, test_session.py, test_chat.py
```

Commands (run from the project root):

- `python -m pcaid.chat "describe the problem"` (the chat; asks for the problem if left out)
  - `--light` / `--heavy` model preference, `--model TAG` to force a model, `--ctx N` context size,
    `--think on` to let the model reason first, `--no-stats` to hide the speed line,
    `--keep-loaded` to skip unloading on exit, `--ollama-url` for a different local port
  - Inside the chat: `/help`, `/info`, `/system`, `/reset`, `/exit`
- `python -m pcaid.profiler` (summary), `--json` (full), `--no-network` (skip internet check)
- `python -m pcaid.selector` (probe this PC, auto), `--light`, `--heavy`, `--profile FILE`
- `python -m unittest discover -s tests -v` (72 tests, about a second)

Before the first chat, download the model once: `ollama pull qwen3.5:4b` (or whichever model
`python -m pcaid.selector` picks). Automatic download and removal is step 7.

## 6. Selector rules (current)

1. A model is **possible** if there is enough free disk (download + 2 GB working room) and
   enough memory: available RAM >= its `min_ram_gb`, or a usable GPU with VRAM >= its
   `min_vram_gb` (and at least 2 GB RAM free), or Apple Silicon with enough free RAM.
2. A possible model is **comfortable** if it runs on a usable GPU, or the CPU has at least
   `cpu_min_threads` threads.
3. `auto` picks the largest comfortable model, or the smallest possible one if none is
   comfortable (with a warning). `light` picks the smallest possible. `heavy` picks the
   largest possible (warning if slow).
4. If no model is possible, the result is "none" with the reason. Unreadable RAM also gives
   "none". Unknown disk or no internet only add warnings.
5. Every result carries plain-language reasons, including why the next size up was not chosen.

Current tiers (`models.json`):

| id | model | Ollama tag | download | min RAM | min VRAM | CPU threads |
|---|---|---|---|---|---|---|
| tiny | Qwen3.5 0.8B | qwen3.5:0.8b | 1.0 GB | 2 GB | 1.5 GB | 2 |
| small | Qwen3.5 2B | qwen3.5:2b | 2.7 GB | 4 GB | 3.5 GB | 4 |
| medium | Qwen3.5 4B | qwen3.5:4b | 3.4 GB | 6 GB | 5 GB | 6 |
| large | Qwen3.5 9B | qwen3.5:9b | 6.6 GB | 9 GB | 9 GB | 12 |

Download sizes come from a third-party summary of the Ollama library (checked 2026-09-30).
RAM, VRAM and thread thresholds are **first guesses**; tune them with the speed numbers the
chat prints after every answer. First real measurements are in section 8.

## 7. How the chat manages the model's memory

- Each request sends: system message + the conversation so far. `num_ctx` is sent every time.
- System message (never dropped): rules, the PC's OS and shell, and the PC summary from the profiler.
- The user's problem is the first user message. If the oldest pairs are dropped, or `/reset` is used,
  the problem is added to the system message so it is never lost.
- Budget for the conversation = 75% of `num_ctx` minus the system message (with room reserved for
  the problem note); the rest is room for the answer.
- A single message too big to ever fit is refused with advice to paste less.
- `/info` shows the context size Ollama is really running with (from `/api/ps`).

## 8. Real measurements (Ubuntu desktop, first real run, 2026-11-11)

| model | speed | first load | notes |
|---|---|---|---|
| Qwen3.5 2B | 18.8 - 20.4 tokens/s | 15.1 s | ignored the OS: gave Windows/PowerShell steps and an invented cmdlet (`get-diagnostics`) on an Ubuntu PC |
| Qwen3.5 4B (selector's `auto` choice) | 10.4 - 11.3 tokens/s | 63.0 s | used the PC facts (Ubuntu, low free disk), gave sensible Linux commands (`df -h`, `free -m`) |

- Not recorded: whether the GPU was used (`ollama ps` shows the CPU/GPU split), and the profiler
  output for that machine, so the speeds cannot yet be tied to hardware or used to tune thresholds.
- A 63 s silent wait looked like a hang -> model is now preloaded with a visible message.
- Both models began their first answer by restating the problem in their own words. Suspected
  cause: the problem was in the system prompt and again as the first message (not proven).
- Both models used Markdown (`**bold**`, code fences), which looks like noise in a terminal.
- Both models asked the user to run commands whose results the tool could simply collect
  (e.g. `df -h && free -m`): this is what step 4 fixes.

## 9. Open questions and known limitations

- **The step-3 fixes below have not been re-run on a real model:** OS/shell line, plain-text rule,
  problem no longer repeated in the system prompt, preload message. Check: does the echo line
  go away, does the 2B stay on Linux, is the output free of `**` and code fences?
- Profiler verified on real Linux (Ubuntu 24.04) and real Windows 11. **macOS is still
  untested on a real Mac** (canned-output tests only).
- Windows: WMI caps GPU memory at 4 GB, so a discrete AMD card (RX 6600M on the Windows laptop)
  shows "VRAM unknown". Exact VRAM currently comes only from `nvidia-smi`.
- Network probe tests a direct TCP connection; behind an HTTP-only proxy it reports "no".
  The selector therefore only warns, it does not refuse.
- macOS "available RAM" is an estimate (free + inactive + speculative pages).
- Not collected yet: CPU instruction set (AVX2 etc.), GPU on Linux without `lspci`,
  pending reboot, uptime, battery.
- Selector has no notion yet of a model already present on disk (offline use, USB, cache).
  Add in step 7 so "no internet" stops blocking when a local copy exists.
- Selector uses currently *available* RAM, so a busy PC gets a smaller model.
- Qwen3.5 supports thinking. The client checks `/api/show` at runtime and only sends
  `think: false` if the model allows switching it off. Whether off hurts answer quality on
  small models is untested.
- Ollama's `prompt_eval_count` may only count uncached tokens, so the context figure in the
  stats line uses our own estimate; the Ollama numbers are used for speed only.
- The model cannot run commands yet; the prompt tells it to ask the user to run read-only
  commands and paste the output. This changes in step 4.
- The system prompt is still a first draft. Tune its wording after seeing more real answers.
- Ollama must already be installed and running; the chat uses `127.0.0.1:11434` unless
  `--ollama-url` is given (the `OLLAMA_HOST` environment variable is not read). Installing and
  removing Ollama itself on a client PC belongs to step 7.
- Exiting unloads the model from memory but does not delete it; deleting is step 7.
- Qwen3.5 downloads carry an Apache 2.0 license layer (per a third-party summary). Confirm the
  license text before distributing to clients.
- `models.json` is read from next to the code. When packaging (step 8) it must be included
  as a data file.
- The client PC needs Python for now. Packaging (step 8) must remove that need.
- Unsigned programs that run commands and download files will trigger Windows
  SmartScreen / Defender and macOS Gatekeeper. Code signing is needed eventually.
- Name `pcaid` is a placeholder.

## 10. Step 4 plan (proposed, not started)

- Tool-calling loop: the model asks for a named tool, the program runs it, the (size-capped) output
  goes back to the model as data, repeat until the model answers. Cap the number of tool calls per
  answer. Tool output is never treated as instructions.
- First toolbox, all read-only, one implementation per OS: `disk_space`, `memory_and_top_processes`,
  `startup_items`, `recent_errors` (Windows event log / journalctl / macOS log), `network_check`,
  `uptime_and_pending_reboot`.
- `ask_user(question, options)`: the model's way to ask for a decision; prints a numbered menu in the
  plain chat for now, real menu in step 6.
- Anything that changes the system waits for step 5 (permissions).
- Decide which tiers may use tools freely and which are limited to the toolbox plus numbered options.

## 11. Log (newest first)

- **2026-11-11**: Step 3 fixes after the first real run (section 8). Changed `prompts.py` (OS and
  shell stated outright, other systems' tools forbidden, plain text requested, "facts were measured,
  do not ask again"; the problem is no longer repeated in the system prompt), `session.py` (the
  problem is added to the system message only after trimming or `/reset`; the budget reserves room
  for it), `ollama_client.py` (new `preload`), `chat.py` (asks for the problem, then loads the model
  with a visible message, passes OS facts to the prompt), `profiler.py` (summary no longer prints the
  Linux version twice, e.g. "Ubuntu 24.04.5 LTS 24.04"). New `tests/test_profiler.py`; fake server
  handles preload. 72 tests pass. Not re-run on a real model yet.
- **2026-11-11**: Step 3 first real run on an Ubuntu desktop with Qwen3.5 2B and 4B (section 8):
  streaming, stats, loading, selector choice (4B) all worked on real Ollama. 4B about 10-11 tokens/s,
  2B about 19-20. Found: 2B ignored the OS, both echoed the problem, Markdown in output, 63 s silent
  first load.
- **2026-11-11**: Step 3 written. New files: `pcaid/ollama_client.py`, `pcaid/prompts.py`,
  `pcaid/session.py`, `pcaid/chat.py`, and tests `tests/fake_ollama.py`, `test_ollama_client.py`,
  `test_session.py`, `test_chat.py`. 60 tests pass. Tested against a fake Ollama server that streams
  chunked line-by-line replies like the real one. API details (fields, `think`, `keep_alive: 0`,
  `/api/show`, `/api/ps`) checked against Ollama's published OpenAPI spec.
- **2026-11-09**: Step 2 done. Wrote `pcaid/selector.py`, `pcaid/models.json` and
  `tests/test_selector.py`. 21 tests pass. On the Windows laptop's numbers the selector picks
  Qwen3.5 4B for `auto` and `--heavy` (9B needs 9 GB RAM, 7.4 GB available) and 0.8B for `--light`.
- **2026-11-09**: Step 1 verified on real Windows 11 (Ryzen 7 5800H, 15.3 GB RAM, RX 6600M).
  All sections correct except discrete AMD VRAM (unknown, see limitations).
- **2026-11-09**: Step 1 done. Wrote `pcaid/profiler.py` and `pcaid/__init__.py`. Ran on the
  Linux sandbox (Ubuntu 24.04); Windows and macOS paths tested with canned output plus a
  deliberately failing probe. All degrade without crashing.
- **2026-11-09**: Project created. Requirements and architecture plan written down.
