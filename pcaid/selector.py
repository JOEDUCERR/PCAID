"""
selector.py - decide which model (if any) this PC should run.

This is pure logic. It takes the profile dict from profiler.py and the model
list from models.json and returns a Selection. It never downloads anything,
never touches the hardware and never calls a model, so it can be tested with
made-up profiles (see tests/test_selector.py).

Rules (see PROJECT.md):
  * Hardware limits are hard. Free memory and free disk decide which models
    are possible at all. A user preference can never push past them.
  * Among the possible models:
        auto  -> the largest one that should run comfortably
        light -> the smallest one
        heavy -> the largest one, even if it may run slowly
  * Every decision comes with plain-language reasons, so the user (and the
    log) can see why a model was chosen or why none was.

Try it:
    python -m pcaid.selector                  # probe this PC, auto choice
    python -m pcaid.selector --light          # force the lightest model
    python -m pcaid.selector --heavy          # largest model that fits
    python -m pcaid.selector --profile p.json # use a saved profile instead
"""
from __future__ import annotations

import argparse
import codecs
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

PREFERENCES = ("auto", "light", "heavy")
MODELS_FILE = Path(__file__).with_name("models.json")

# Only these GPUs count as "can speed up the model". NVIDIA needs the CUDA
# driver, which nvidia-smi (used by the profiler) confirms. Apple Silicon is
# handled separately as unified memory. AMD and Intel GPUs are ignored until a
# runtime that supports them is chosen (PROJECT.md, step 7).
ACCELERATED_VENDORS = ("NVIDIA",)

# Even when the model sits in GPU memory, the runtime and OS still need some RAM.
MIN_RAM_WITH_GPU_GB = 2.0


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelSpec:
    """One entry of models.json. Field names must match the JSON keys."""
    id: str
    name: str
    ollama_tag: str
    download_gb: float
    min_ram_gb: float       # available RAM needed to run on the CPU
    min_vram_gb: float      # GPU memory needed to run fully on the GPU
    cpu_min_threads: int    # CPU threads wanted for comfortable CPU-only speed


@dataclass(frozen=True)
class Capacity:
    """What this PC offers, read from the profile. None means unknown."""
    free_ram_gb: Optional[float]
    total_ram_gb: Optional[float]
    threads: Optional[int]
    accelerator: str                # "none", "discrete" or "unified"
    vram_gb: Optional[float]        # best usable discrete GPU, else None
    free_disk_gb: Optional[float]
    internet: Optional[bool]


@dataclass(frozen=True)
class Assessment:
    """How one model fares on this PC."""
    model: ModelSpec
    possible: bool       # hard limits (memory, disk) are met
    comfortable: bool    # and it should also run at a usable speed
    problem: str         # why it is not possible / not comfortable ('' if fine)


@dataclass(frozen=True)
class Selection:
    model: Optional[ModelSpec]      # None = no model can run on this PC
    preference: str
    reasons: list[str]
    warnings: list[str]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_models(path: Path = MODELS_FILE) -> tuple[list[ModelSpec], float]:
    """Read models.json. Returns (models smallest-first, disk headroom in GB)."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)

    try:
        models = [ModelSpec(**entry) for entry in data["models"]]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{path.name}: bad model entry ({exc})") from exc

    if not models:
        raise ValueError(f"{path.name}: no models listed")
    if len({model.id for model in models}) != len(models):
        raise ValueError(f"{path.name}: model ids must be unique")
    for smaller, larger in zip(models, models[1:]):
        if larger.min_ram_gb <= smaller.min_ram_gb:
            raise ValueError(
                f"{path.name}: list models smallest first "
                f"({larger.id} needs no more RAM than {smaller.id})"
            )
    return models, float(data.get("disk_headroom_gb", 2.0))


def _read_json(path: str) -> dict[str, Any]:
    """Read a JSON file written by either Python or Windows PowerShell.

    PowerShell's '>' operator saves UTF-16, which json.load cannot read.
    """
    raw = Path(path).read_bytes()
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig")
    return json.loads(text)


# ---------------------------------------------------------------------------
# Reading the profile
# ---------------------------------------------------------------------------

def read_capacity(profile: dict[str, Any]) -> Capacity:
    memory = profile.get("memory") or {}
    cpu = profile.get("cpu") or {}
    temp_disk = (profile.get("disk") or {}).get("temp_dir") or {}
    network = profile.get("network")
    gpus = profile.get("gpus") or []

    # Discrete GPUs we can use: NVIDIA with a known VRAM size.
    vram_sizes = [
        gpu["vram_gb"]
        for gpu in gpus
        if gpu.get("vendor") in ACCELERATED_VENDORS
        and not gpu.get("unified_memory")
        and gpu.get("vram_gb")
    ]
    if vram_sizes:
        accelerator, vram = "discrete", max(vram_sizes)
    elif any(gpu.get("unified_memory") for gpu in gpus):
        accelerator, vram = "unified", None
    else:
        accelerator, vram = "none", None

    internet = None
    if network is not None:
        internet = any((network.get("model_hosts_reachable") or {}).values())

    return Capacity(
        free_ram_gb=memory.get("available_gb"),
        total_ram_gb=memory.get("total_gb"),
        threads=cpu.get("logical_cores"),
        accelerator=accelerator,
        vram_gb=vram,
        free_disk_gb=temp_disk.get("free_gb"),
        internet=internet,
    )


# ---------------------------------------------------------------------------
# Assessing models
# ---------------------------------------------------------------------------

def _assess(model: ModelSpec, cap: Capacity, headroom_gb: float) -> Assessment:
    """Check one model against this PC's capacity."""
    # Disk. Unknown free space does not block; select_model() adds a warning.
    needed_disk = model.download_gb + headroom_gb
    if cap.free_disk_gb is not None and cap.free_disk_gb < needed_disk:
        return Assessment(
            model, False, False,
            f"needs {needed_disk:.1f} GB free disk space (download plus working room); "
            f"{cap.free_disk_gb:.1f} GB is free",
        )

    # Memory: can it run on the CPU, or on the GPU?
    free_ram = cap.free_ram_gb
    ram_ok = free_ram is not None and free_ram >= model.min_ram_gb
    on_gpu = (
        (
            cap.accelerator == "discrete"
            and (cap.vram_gb or 0) >= model.min_vram_gb
            and free_ram is not None
            and free_ram >= MIN_RAM_WITH_GPU_GB
        )
        or (cap.accelerator == "unified" and ram_ok)
    )

    if not ram_ok and not on_gpu:
        if free_ram is None:
            problem = "cannot be checked because available RAM could not be read"
        else:
            problem = f"needs {model.min_ram_gb:.1f} GB available RAM; {free_ram:.1f} GB is available"
            if cap.accelerator == "discrete":
                problem += (
                    f" (the GPU has {cap.vram_gb:.1f} GB VRAM; "
                    f"it needs {model.min_vram_gb:.1f} GB to run there)"
                )
        return Assessment(model, False, False, problem)

    # Speed: on the CPU alone, few threads means slow answers.
    if not on_gpu and cap.threads is not None and cap.threads < model.cpu_min_threads:
        return Assessment(
            model, True, False,
            f"on the CPU alone it wants {model.cpu_min_threads}+ threads; "
            f"this PC has {cap.threads}, so it would be slow",
        )
    return Assessment(model, True, True, "")


def _gb_text(value: Optional[float]) -> str:
    return "unknown" if value is None else f"{value:.1f} GB"


def _describe_capacity(cap: Capacity) -> list[str]:
    if cap.accelerator == "discrete":
        gpu_line = f"GPU acceleration: NVIDIA GPU with {_gb_text(cap.vram_gb)} VRAM."
    elif cap.accelerator == "unified":
        gpu_line = "GPU acceleration: Apple Silicon (shares system memory)."
    else:
        gpu_line = "GPU acceleration: none usable (only NVIDIA GPUs and Apple Silicon are used for now)."
    return [
        f"Available RAM: {_gb_text(cap.free_ram_gb)} of {_gb_text(cap.total_ram_gb)} total.",
        f"CPU threads: {'unknown' if cap.threads is None else cap.threads}.",
        gpu_line,
        f"Free disk space for the download: {_gb_text(cap.free_disk_gb)}.",
    ]


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

def select_model(
    profile: dict[str, Any],
    models: list[ModelSpec],
    preference: str = "auto",
    disk_headroom_gb: float = 2.0,
) -> Selection:
    """Pick a model for this PC. `models` must be ordered smallest first."""
    if preference not in PREFERENCES:
        raise ValueError(f"preference must be one of {PREFERENCES}, not {preference!r}")

    cap = read_capacity(profile)
    reasons = _describe_capacity(cap)
    warnings: list[str] = []
    if cap.free_disk_gb is None:
        warnings.append("Free disk space could not be read; the download may fail if space is short.")
    if cap.internet is False:
        warnings.append(
            "The model download sites could not be reached (no internet, or a proxy "
            "blocks direct connections); the download may fail."
        )

    assessments = [_assess(model, cap, disk_headroom_gb) for model in models]
    possible = [a for a in assessments if a.possible]
    comfortable = [a for a in possible if a.comfortable]

    if not possible:
        smallest = assessments[0]
        reasons.append(f"No model can run on this PC. Even the smallest, {smallest.model.name}, {smallest.problem}.")
        return Selection(None, preference, reasons, warnings)

    if preference == "light":
        chosen, why = possible[0], "the smallest model, as requested (light)."
    elif preference == "heavy":
        chosen, why = possible[-1], "the largest model this PC can hold, as requested (heavy)."
    elif comfortable:
        chosen, why = comfortable[-1], "the largest model that should run comfortably here."
    else:
        chosen, why = possible[0], "no model should run comfortably here, so the smallest one."
    reasons.append(f"Chose {chosen.model.name}: {why}")

    if not chosen.comfortable:
        warnings.append(f"{chosen.model.name}: {chosen.problem}.")

    # Explain why the next size up was not chosen (not needed for 'light').
    index = assessments.index(chosen)
    if preference != "light" and index + 1 < len(assessments):
        bigger = assessments[index + 1]
        if not bigger.possible:
            reasons.append(f"Next size up, {bigger.model.name}, {bigger.problem}.")
        elif not bigger.comfortable:
            reasons.append(f"Next size up, {bigger.model.name}, fits in memory but {bigger.problem}.")

    return Selection(chosen.model, preference, reasons, warnings)


def format_selection(selection: Selection) -> str:
    """Human-readable text for the terminal."""
    model = selection.model
    if model is None:
        lines = ["Selected model: none"]
    else:
        lines = [
            f"Selected model: {model.name} [{model.id}] "
            f"(Ollama tag {model.ollama_tag}, download about {model.download_gb:.1f} GB)"
        ]
    lines.append(f"Preference: {selection.preference}")
    lines.append("Why:")
    lines.extend(f"  - {reason}" for reason in selection.reasons)
    if selection.warnings:
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in selection.warnings)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Choose a model for this PC.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--light", action="store_true", help="use the lightest model")
    group.add_argument("--heavy", action="store_true", help="use the largest model that fits")
    parser.add_argument("--profile", metavar="FILE", help="read a saved profile (JSON) instead of probing this PC")
    parser.add_argument("--no-network", action="store_true", help="skip the internet check when probing")
    args = parser.parse_args()

    preference = "light" if args.light else "heavy" if args.heavy else "auto"

    if args.profile:
        profile = _read_json(args.profile)
    else:
        from .profiler import collect_profile  # imported here so tests need no OS probing
        profile = collect_profile(check_network=not args.no_network)

    models, headroom = load_models()
    print(format_selection(select_model(profile, models, preference, headroom)))


if __name__ == "__main__":
    main()
