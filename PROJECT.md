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
| System message = rules + PC facts + the original problem; sent every time, never trimmed | Done |
| When history is too long, oldest user+assistant pairs are dropped first; budget is 75% of `num_ctx` | Done |
| Token counts are estimates (characters / 3, pessimistic) | Done; revisit if it misjudges |
| Thinking mode is switched off by default when the model allows it (checked via `/api/show`); `--think on` enables it | Proposed (verify speed and quality on the real model) |
| Chat is a plain `input()`/`print()` loop; the real terminal UI is step 6 | Agreed |
| Ctrl+C during an answer stops it and drops that question; Ctrl+C at the prompt quits | Done |
| On exit the model is unloaded from memory (files stay on disk until step 7) | Done |
| `--model TAG` bypasses the selector (for comparing models and tuning thresholds) | Done |
| The model gets a toolbox of named, tested functions, not a raw shell | Proposed |
| Three permission tiers: read-only auto-runs; changes need a yes; destructive actions blocked | Proposed |
| Weak-PC fallback ladder: no-AI runbooks, tiny model, remote model on LAN, offline model from USB | Proposed |
| Python 3.9+ for the prototype | Proposed |
| Prototype on Ollama behind a runtime interface; later swap to llama.cpp in a temp folder for easy cleanup | Proposed |

## 4. Build plan

- [x] 1. Profiler: hardware/software facts as compact JSON + short text summary
- [x] 2. Model selector: pure function (profile + preference -> model or none), config-driven
- [~] 3. Chat loop: profile summary as system prompt, explicit context size, streaming, trimming
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
  ollama_client.py   # step 3: talk to the local Ollama server
  prompts.py         # step 3: the system prompt
  session.py         # step 3: history, context budget, trimming
  chat.py            # step 3: the terminal chat program
tests/
  fake_ollama.py     # step 3: fake Ollama server used by the tests (not part of the product)
  test_selector.py   # 21 tests
  test_ollama_client.py, test_session.py, test_chat.py   # 39 tests
```

Commands (run from the project root):

- `python -m pcaid.chat "describe the problem"` (the chat; asks for the problem if left out)
  - `--light` / `--heavy` model preference, `--model TAG` to force a model, `--ctx N` context size,
    `--think on` to let the model reason first, `--no-stats` to hide the speed line,
    `--keep-loaded` to skip unloading on exit, `--ollama-url` for a different local port
  - Inside the chat: `/help`, `/info`, `/system`, `/reset`, `/exit`
- `python -m pcaid.profiler` (summary), `--json` (full), `--no-network` (skip internet check)
- `python -m pcaid.selector` (probe this PC, auto), `--light`, `--heavy`, `--profile FILE`
- `python -m unittest discover -s tests -v` (60 tests, about a second)

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
chat now prints after every answer.

## 7. How the chat manages the model's memory (step 3)

- Each request sends: system message + the conversation so far. `num_ctx` is sent every time.
- System message (never dropped): rules, the PC summary from the profiler, and the original problem.
- Budget for the conversation = 75% of `num_ctx` minus the system message; the rest is room for the answer.
- Over budget: oldest user+assistant pair is dropped, repeatedly; the chat prints a notice.
- A single message too big to ever fit is refused with advice to paste less.
- `/info` shows the context size Ollama is really running with (from `/api/ps`), to confirm
  that `num_ctx` took effect.

## 8. Open questions and known limitations

- **Step 3 has never run against a real model.** It is tested against a fake Ollama server that
  speaks the same streaming protocol, which proves the code, not the model. To check on the
  laptop: speed (tokens/s) on CPU for 0.8B / 2B / 4B, whether `/info` shows 8192, whether
  `think: false` is accepted, and whether the answers are useful.
- Profiler verified on real Linux (Ubuntu 24.04) and real Windows 11. **macOS is still
  untested on a real Mac** (canned-output tests only).
- Windows: WMI caps GPU memory at 4 GB, so a discrete AMD card (RX 6600M on the first
  Windows test PC) shows "VRAM unknown". Exact VRAM currently comes only from `nvidia-smi`.
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
- Ollama's `prompt_eval_count` may only count uncached tokens, so the context figure shown in the
  stats line uses our own estimate instead; the Ollama numbers are used for speed only.
- The model cannot run commands yet; the system prompt tells it to ask the user to run
  read-only commands and paste the output. This changes in step 4.
- The system prompt is a first draft. Tune its wording after seeing real answers.
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

## 9. Log (newest first)

- **2026-11-11**: Step 3 written. New files: `pcaid/ollama_client.py`, `pcaid/prompts.py`,
  `pcaid/session.py`, `pcaid/chat.py`, and tests `tests/fake_ollama.py`, `test_ollama_client.py`,
  `test_session.py`, `test_chat.py`. 60 tests pass (21 selector + 39 new), run in about a second.
  Tested against a fake Ollama server that streams chunked line-by-line replies like the real one:
  streaming arrives incrementally, context size is always sent, `think` only when allowed,
  Ctrl+C-style early stop removes the unanswered question, trimming never drops the system message,
  the client refuses non-local addresses and ignores proxy variables, missing model / Ollama not
  running / oversized paste / no model fits all give clear messages. API details (fields, `think`,
  `keep_alive: 0`, `/api/show`, `/api/ps`) were checked against Ollama's published OpenAPI spec.
  **Not yet run against a real model.**
- **2026-11-09**: Step 2 done. Wrote `pcaid/selector.py`, `pcaid/models.json` and
  `tests/test_selector.py`. 21 tests pass. On the Windows laptop's numbers the selector picks
  Qwen3.5 4B for `auto` and `--heavy` (9B needs 9 GB RAM, 7.4 GB available) and 0.8B for `--light`.
- **2026-11-09**: Step 1 verified on real Windows 11 (Ryzen 7 5800H, 15.3 GB RAM, RX 6600M).
  All sections correct except discrete AMD VRAM (unknown, see limitations).
- **2026-11-09**: Step 1 done. Wrote `pcaid/profiler.py` and `pcaid/__init__.py`. Ran on the
  Linux sandbox (Ubuntu 24.04); Windows and macOS paths tested with canned output plus a
  deliberately failing probe. All degrade without crashing.
- **2026-11-09**: Project created. Requirements and architecture plan written down.
