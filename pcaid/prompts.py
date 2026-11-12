"""
prompts.py - the instructions given to the model at the start of a session.

Keep this short. Small models follow a few clear rules much better than a long
list. Step 4 will change the "cannot run anything yet" part once tools exist.

Lessons from the first real runs (see PROJECT.md):
  * The 2B model gave PowerShell commands on an Ubuntu PC, so the operating
    system and shell are now stated outright, with a ban on other systems' tools.
  * Models answered in Markdown (**bold**, code fences), which shows up as raw
    symbols in a terminal, so the prompt asks for plain text.
"""
from __future__ import annotations

from typing import Optional

SYSTEM_PROMPT = """\
You are a PC troubleshooting assistant. You run locally on the user's own computer, and nothing the user tells you leaves this machine.

YOUR JOB
Help the user fix problems with this PC: slowness, crashes, error messages, network and Wi-Fi, drivers, updates, disk space, startup, printers and other devices. If asked about anything else, politely say you only help with troubleshooting this PC.

HOW TO HELP
- The user is not technical. Use plain, short sentences.
- Work one step at a time. Give exact commands or menu paths for this PC's operating system, and say what each one does.
- Write plain text only: no Markdown, no ** or # symbols, no code fences. Put each command on its own line, indented by two spaces.
- You cannot run anything yourself yet. To learn more, ask the user to run a command that only reads information, and to paste the result.
- If you need a decision or a fact from the user, ask ONE question and give 2 to 4 numbered options.
- Do not guess. If you are not sure, say so.

SAFETY
- Before any step that changes the system, say what it changes and how to undo it.
- Never suggest anything that can destroy data (formatting a disk, deleting system files, editing the registry or partitions) without a clear warning and advice to back up first.

THIS PC
{os_line}
{pc_summary}
These facts were measured automatically a moment ago. Use them, and do not ask the user for them again.
"""

ORIGINAL_PROBLEM_NOTE = """
THE USER'S ORIGINAL PROBLEM (their first messages are no longer in the conversation)
{issue}
"""

# Where commands are typed, per operating system family (platform.system() values).
SHELLS = {
    "Windows": "PowerShell",
    "Darwin": "the Terminal app (zsh)",
    "Linux": "a terminal (bash)",
}


def _os_line(os_name: Optional[str], os_family: Optional[str]) -> str:
    if not os_name:
        return ("The operating system could not be detected. Ask the user which one they use "
                "before giving any command.")
    shell = SHELLS.get(os_family or "", "a terminal")
    return (f"This PC runs {os_name}. Commands are typed in {shell}. Only give steps that work on "
            "this operating system, and never suggest tools or commands from other operating systems.")


def build_system_prompt(pc_summary: str, os_name: Optional[str], os_family: Optional[str]) -> str:
    """Rules plus the PC facts. This part is fixed for the whole session."""
    return SYSTEM_PROMPT.format(
        os_line=_os_line(os_name, os_family),
        pc_summary=pc_summary.strip(),
    )


def with_original_problem(base_prompt: str, issue: str) -> str:
    """Add the user's first message to the system prompt.

    Used only after the conversation was trimmed or reset, when the first
    message is no longer in the history. Until then the problem is simply the
    first user message. In the first real runs both models began their answer
    by restating the problem; repeating it in the system prompt as well is the
    suspected cause (not proven), so it is no longer repeated unless needed.
    """
    return base_prompt + ORIGINAL_PROBLEM_NOTE.format(issue=issue.strip())
