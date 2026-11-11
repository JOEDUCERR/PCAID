# PROJECT.md: pcaid (working name)

_Last updated: 2026-11-09_

> Update rule: this file is updated at the end of **every** completed piece of work
> (new file, change, test result, decision). Newest log entry goes first.

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
| Build starts with the hardware/software profiler | Agreed |
| Keep this PROJECT.md, updated after every piece of work | Agreed |
| Code is built and delivered file by file, reviewed and integrated manually | Agreed |
| Hardware picks the model ceiling; issue type and `--light`/`--heavy` flags adjust within it | Proposed |
| The model gets a toolbox of named, tested functions, not a raw shell | Proposed |
| Three permission tiers: read-only auto-runs; changes need a yes; destructive actions blocked | Proposed |
| Weak-PC fallback ladder: no-AI runbooks, tiny model, remote model on LAN, offline model from USB | Proposed |
| Python 3.9+ for the prototype | Proposed |
| Prototype on Ollama behind a runtime interface; later swap to llama.cpp in a temp folder for easy cleanup | Proposed |
| Model list lives in a config file, not in code | Proposed |
| Profiler uses the standard library only | Done |
| Profiler collects no hostname, username, serials, IP/MAC or file paths | Done |

## 4. Build plan

- [x] 1. Profiler: hardware/software facts as compact JSON + short text summary
- [ ] 2. Model selector: pure function (profile + flags -> tier), config-driven
- [ ] 3. Chat loop with the profile summary as system prompt (set context size explicitly)
- [ ] 4. Read-only tools + tool-calling loop
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
  profiler.py     # phase 1
```

Run the profiler: `python -m pcaid.profiler` (summary) or `python -m pcaid.profiler --json`.
Flag `--no-network` skips the internet check.

## 6. Open questions and known limitations

- Profiler is verified on real Linux (Ubuntu 24.04). Windows and macOS paths were only
  tested against canned command output. **Real runs on both are still needed.**
- Windows: WMI reports GPU memory capped at 4 GB; the profiler treats a value near the
  cap as unknown. Exact VRAM currently comes only from `nvidia-smi` (NVIDIA).
- Network probe tests a direct TCP connection; behind an HTTP-only proxy it reports
  "no" even if downloads could work.
- macOS "available RAM" is an estimate (free + inactive + speculative pages).
- Not collected yet: CPU instruction set (AVX2 etc.), GPU on Linux without `lspci`,
  pending reboot, uptime, battery.
- The client PC needs Python for now. Packaging (phase 8) must remove that need.
- Unsigned programs that run commands and download files will trigger Windows
  SmartScreen / Defender and macOS Gatekeeper. Code signing is needed eventually.
- Name `pcaid` is a placeholder.
- Which model family to use per tier is undecided. Candidate: one family with sizes
  of roughly 0.8B / 2B / 4B / 9B so tool-call format stays identical across tiers.
  Verify on real hardware.

## 7. Log (newest first)

- **2026-11-09**: Phase 1 done. Wrote `pcaid/profiler.py` and `pcaid/__init__.py`.
  Ran on the Linux sandbox (Ubuntu 24.04): OS, CPU, RAM, disk, privileges, package
  managers all detected; no GPU or network there. Tested Windows (PowerShell JSON,
  single-GPU case, `nvidia-smi` override, PowerShell missing) and macOS Apple Silicon
  paths with canned output, plus a deliberately failing probe. All degrade without
  crashing. Real Windows/macOS runs pending.
- **2026-11-09**: Project created. Requirements and architecture plan written down.
