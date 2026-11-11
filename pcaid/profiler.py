"""
profiler.py - collect a compact hardware/software profile of this PC.

Rules this module follows (see PROJECT.md):
  * Standard library only. The tool must run on a fresh client PC with
    nothing extra installed.
  * Never crash. Any single probe can fail (missing command, odd OS
    version, no permission). Failures are recorded under "probe_errors"
    and the rest of the profile is still returned.
  * Privacy. No hostname, username, serial numbers, IP/MAC addresses or
    file paths are collected, because this data ends up in the model's
    context and in session logs.

Try it on the current machine:
    python -m pcaid.profiler            # short text summary
    python -m pcaid.profiler --json     # full profile as JSON
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import tempfile
from functools import lru_cache
from typing import Any, Callable, Optional

MB = 1024 ** 2
GB = 1024 ** 3

# Hosts we expect to download models from. The network probe only checks
# that a TCP connection to port 443 works.
MODEL_HOSTS = ("huggingface.co", "registry.ollama.ai")

PACKAGE_MANAGERS = ("winget", "choco", "brew", "apt", "dnf", "pacman", "snap")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str], timeout: int = 15) -> Optional[str]:
    """Run a command and return its stdout, or None if anything goes wrong.

    "Wrong" means: command not found, timeout, or a non-zero exit code.
    Callers treat None as "this fact is unknown".
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def read_file(path: str) -> Optional[str]:
    """Read a text file, or return None if it cannot be read."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _to_int(text: Any) -> Optional[int]:
    try:
        return int(str(text).strip())
    except ValueError:
        return None


def _clean(text: Optional[str]) -> Optional[str]:
    """Collapse runs of whitespace; return None for empty input."""
    if not text:
        return None
    return " ".join(str(text).split()) or None


def _gb(num_bytes: Optional[int]) -> Optional[float]:
    return round(num_bytes / GB, 1) if num_bytes is not None else None


def _kb_to_bytes(value: Any) -> Optional[int]:
    kb = _to_int(value) if value is not None else None
    return kb * 1024 if kb is not None else None


def _as_list(value: Any) -> list:
    """PowerShell's JSON gives a bare object for one item and a list for many."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _parse_size_gb(text: Optional[str]) -> Optional[float]:
    """Turn strings like '1536 MB' or '8 GB' into gigabytes."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(MB|GB)", text or "", re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    if match.group(2).upper() == "MB":
        value /= 1024
    return round(value, 1)


def _vendor(name: Optional[str]) -> str:
    lowered = (name or "").lower()
    if any(word in lowered for word in ("nvidia", "geforce", "quadro", "rtx", "gtx")):
        return "NVIDIA"
    if any(word in lowered for word in ("amd", "radeon", "advanced micro devices")):
        return "AMD"
    if "intel" in lowered:
        return "Intel"
    if "apple" in lowered:
        return "Apple"
    return "Unknown"


def _gpu_entry(
    name: Optional[str],
    vram_gb: Optional[float] = None,
    unified_memory: bool = False,
    driver: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "vendor": _vendor(name),
        "vram_gb": vram_gb,
        "unified_memory": unified_memory,  # True on Apple Silicon: GPU shares system RAM
        "driver": driver,
    }


# ---------------------------------------------------------------------------
# Windows: one PowerShell call, cached
# ---------------------------------------------------------------------------
# Starting PowerShell takes about a second, so we ask for everything we need
# in a single call and let each probe pick its part out of the result.

_WINDOWS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$os   = Get-CimInstance Win32_OperatingSystem |
        Select-Object Caption, Version, BuildNumber, FreePhysicalMemory, TotalVisibleMemorySize
$cpu  = Get-CimInstance Win32_Processor |
        Select-Object -First 1 Name, NumberOfCores, NumberOfLogicalProcessors
$gpus = @(Get-CimInstance Win32_VideoController |
        Select-Object Name, AdapterRAM, DriverVersion)
@{ os = $os; cpu = $cpu; gpus = $gpus } | ConvertTo-Json -Depth 4 -Compress
"""


@lru_cache(maxsize=1)
def _windows_cim() -> dict[str, Any]:
    out = run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_SCRIPT],
        timeout=30,
    )
    if not out:
        return {}
    try:
        return json.loads(out.lstrip("\ufeff"))
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Probes: each returns plain data (dict / list) and may raise; the caller
# collect_profile() catches the exception.
# ---------------------------------------------------------------------------

def _read_os_release() -> dict[str, str]:
    """Parse /etc/os-release (Linux) into a dict."""
    values: dict[str, str] = {}
    for line in (read_file("/etc/os-release") or "").splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"')
    return values


def probe_os() -> dict[str, Any]:
    system = platform.system()  # "Windows", "Darwin" (macOS) or "Linux"
    info: dict[str, Any] = {"family": system, "arch": platform.machine()}

    if system == "Windows":
        cim_os = _windows_cim().get("os") or {}
        info["name"] = _clean(cim_os.get("Caption")) or platform.platform()
        info["version"] = cim_os.get("Version") or platform.version()
        info["build"] = cim_os.get("BuildNumber")
    elif system == "Darwin":
        info["name"] = "macOS"
        info["version"] = run(["sw_vers", "-productVersion"])
    elif system == "Linux":
        release = _read_os_release()
        info["name"] = release.get("PRETTY_NAME") or "Linux"
        info["version"] = release.get("VERSION_ID")
        info["kernel"] = platform.release()
    else:
        info["name"] = platform.platform()
    return info


def _linux_physical_cores(cpuinfo: str) -> Optional[int]:
    """'cpu cores' is per socket, so multiply by the number of sockets.

    ARM machines do not report these fields; we return None for them.
    """
    cores = re.search(r"^cpu cores\s*:\s*(\d+)", cpuinfo, re.MULTILINE)
    if not cores:
        return None
    sockets = set(re.findall(r"^physical id\s*:\s*(\d+)", cpuinfo, re.MULTILINE))
    return int(cores.group(1)) * max(len(sockets), 1)


def probe_cpu() -> dict[str, Any]:
    system = platform.system()
    cpu: dict[str, Any] = {
        "model": None,
        "physical_cores": None,
        "logical_cores": os.cpu_count(),
    }

    if system == "Windows":
        win_cpu = _windows_cim().get("cpu") or {}
        cpu["model"] = _clean(win_cpu.get("Name"))
        cpu["physical_cores"] = win_cpu.get("NumberOfCores")
        cpu["logical_cores"] = win_cpu.get("NumberOfLogicalProcessors") or cpu["logical_cores"]
    elif system == "Darwin":
        cpu["model"] = _clean(run(["sysctl", "-n", "machdep.cpu.brand_string"]))
        cpu["physical_cores"] = _to_int(run(["sysctl", "-n", "hw.physicalcpu"]))
    elif system == "Linux":
        cpuinfo = read_file("/proc/cpuinfo") or ""
        match = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
        cpu["model"] = _clean(match.group(1)) if match else None
        cpu["physical_cores"] = _linux_physical_cores(cpuinfo)
    return cpu


def _meminfo_bytes(meminfo: str, key: str) -> Optional[int]:
    match = re.search(rf"^{key}:\s+(\d+)\s*kB", meminfo, re.MULTILINE)
    return int(match.group(1)) * 1024 if match else None


def _macos_available_memory() -> Optional[int]:
    """Estimate available RAM on macOS as free + inactive + speculative pages."""
    out = run(["vm_stat"])
    if not out:
        return None
    page = re.search(r"page size of (\d+) bytes", out)
    if not page:
        return None
    pages = 0
    for label in ("Pages free", "Pages inactive", "Pages speculative"):
        match = re.search(rf"^{label}:\s+(\d+)", out, re.MULTILINE)
        if match:
            pages += int(match.group(1))
    return pages * int(page.group(1))


def probe_memory() -> dict[str, Any]:
    system = platform.system()
    total: Optional[int] = None      # bytes
    available: Optional[int] = None  # bytes

    if system == "Windows":
        win_os = _windows_cim().get("os") or {}
        total = _kb_to_bytes(win_os.get("TotalVisibleMemorySize"))
        available = _kb_to_bytes(win_os.get("FreePhysicalMemory"))
    elif system == "Darwin":
        total = _to_int(run(["sysctl", "-n", "hw.memsize"]))
        available = _macos_available_memory()
    elif system == "Linux":
        meminfo = read_file("/proc/meminfo") or ""
        total = _meminfo_bytes(meminfo, "MemTotal")
        available = _meminfo_bytes(meminfo, "MemAvailable")
    return {"total_gb": _gb(total), "available_gb": _gb(available)}


def _nvidia_smi_gpus() -> list[dict[str, Any]]:
    """Accurate NVIDIA info on any OS, if the NVIDIA driver is installed."""
    out = run([
        "nvidia-smi",
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return []
    gpus = []
    for line in out.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 3:
            continue
        name, memory_mib, driver = parts
        mib = _to_int(memory_mib)
        gpus.append(_gpu_entry(name, vram_gb=round(mib / 1024, 1) if mib else None, driver=driver))
    return gpus


def _windows_gpus() -> list[dict[str, Any]]:
    gpus = []
    for item in _as_list(_windows_cim().get("gpus")):
        adapter_ram = item.get("AdapterRAM")
        # WMI stores AdapterRAM in 32 bits, so anything at the 4 GB ceiling is
        # a cap, not the real size. Treat it as unknown.
        if not isinstance(adapter_ram, int) or adapter_ram >= 4 * GB - MB:
            vram = None
        else:
            vram = _gb(adapter_ram)
        gpus.append(_gpu_entry(_clean(item.get("Name")), vram_gb=vram, driver=item.get("DriverVersion")))
    return gpus


def _macos_gpus() -> list[dict[str, Any]]:
    out = run(["system_profiler", "SPDisplaysDataType", "-json"], timeout=30)
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    apple_silicon = platform.machine() == "arm64"
    gpus = []
    for entry in data.get("SPDisplaysDataType", []):
        name = _clean(entry.get("sppci_model") or entry.get("_name"))
        vram = _parse_size_gb(entry.get("spdisplays_vram") or entry.get("spdisplays_vram_shared"))
        gpus.append(_gpu_entry(name, vram_gb=vram, unified_memory=apple_silicon))
    return gpus


def _linux_gpus() -> list[dict[str, Any]]:
    out = run(["lspci"])  # needs the 'pciutils' package; missing on some minimal installs
    if not out:
        return []
    gpus = []
    for line in out.splitlines():
        if not re.search(r"VGA compatible controller|3D controller|Display controller", line):
            continue
        name = line.split(": ", 1)[1] if ": " in line else None
        name = re.sub(r"\s*\(rev [0-9a-f]+\)$", "", name or "")
        gpus.append(_gpu_entry(_clean(name)))
    return gpus


def probe_gpus() -> list[dict[str, Any]]:
    system = platform.system()
    if system == "Windows":
        gpus = _windows_gpus()
    elif system == "Darwin":
        gpus = _macos_gpus()
    elif system == "Linux":
        gpus = _linux_gpus()
    else:
        gpus = []

    # nvidia-smi knows the real VRAM size, so prefer it over the OS-level guess.
    nvidia = _nvidia_smi_gpus()
    if nvidia:
        gpus = [gpu for gpu in gpus if gpu["vendor"] != "NVIDIA"] + nvidia
    return gpus


def _disk_usage(path: str) -> dict[str, Any]:
    # We report sizes only, never the path itself: on Windows the temp path
    # contains the user name.
    usage = shutil.disk_usage(path)
    return {"total_gb": _gb(usage.total), "free_gb": _gb(usage.free)}


def probe_disk() -> dict[str, Any]:
    if platform.system() == "Windows":
        system_root = os.environ.get("SystemDrive", "C:") + "\\"
    else:
        system_root = "/"
    return {
        "system_drive": _disk_usage(system_root),
        "temp_dir": _disk_usage(tempfile.gettempdir()),  # where a model download would likely go
    }


def _is_admin() -> Optional[bool]:
    """True if running as root (Linux/macOS) or in an elevated process (Windows)."""
    try:
        if platform.system() == "Windows":
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        return os.geteuid() == 0
    except (AttributeError, OSError):
        return None


def probe_software() -> dict[str, Any]:
    return {
        "is_admin": _is_admin(),
        "ollama_installed": shutil.which("ollama") is not None,
        "package_managers": [name for name in PACKAGE_MANAGERS if shutil.which(name)],
    }


def probe_network(timeout: float = 3.0) -> dict[str, Any]:
    """Can we open a TCP connection to the hosts models are downloaded from?

    Limitation: on a network that only allows internet access through an HTTP
    proxy, this reports False even though downloads could work via the proxy.
    """
    reachable = {}
    for host in MODEL_HOSTS:
        try:
            with socket.create_connection((host, 443), timeout=timeout):
                reachable[host] = True
        except OSError:  # DNS failure, refused connection, timeout, ...
            reachable[host] = False
    return {"model_hosts_reachable": reachable}


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------

def collect_profile(check_network: bool = True) -> dict[str, Any]:
    """Run every probe and return one dict. Never raises."""
    probes: dict[str, Callable[[], Any]] = {
        "os": probe_os,
        "cpu": probe_cpu,
        "memory": probe_memory,
        "gpus": probe_gpus,
        "disk": probe_disk,
        "software": probe_software,
    }
    if check_network:
        probes["network"] = probe_network

    profile: dict[str, Any] = {"schema_version": 1}
    errors: dict[str, str] = {}
    for name, probe in probes.items():
        try:
            profile[name] = probe()
        except Exception as exc:  # deliberate: one broken probe must not kill the tool
            profile[name] = None
            errors[name] = f"{type(exc).__name__}: {exc}"
    if errors:
        profile["probe_errors"] = errors
    return profile


def _v(value: Any, default: str = "unknown") -> str:
    return default if value is None else str(value)


def _yes_no(value: Optional[bool]) -> str:
    return "unknown" if value is None else ("yes" if value else "no")


def _describe_gpu(gpu: dict[str, Any]) -> str:
    if gpu.get("unified_memory"):
        memory = "unified memory"
    elif gpu.get("vram_gb") is not None:
        memory = f"{gpu['vram_gb']} GB VRAM"
    else:
        memory = "VRAM unknown"
    return f"{_v(gpu.get('name'))} ({memory})"


def summarize(profile: dict[str, Any]) -> str:
    """Short plain-text version of the profile, meant for the model's system prompt.

    Kept deliberately small: small models cope badly with long context.
    """
    os_info = profile.get("os") or {}
    cpu = profile.get("cpu") or {}
    mem = profile.get("memory") or {}
    disk = profile.get("disk") or {}
    sys_drive = disk.get("system_drive") or {}
    temp = disk.get("temp_dir") or {}
    software = profile.get("software") or {}
    gpus = profile.get("gpus") or []

    os_line = f"OS: {_v(os_info.get('name'))} {_v(os_info.get('version'), '')}".rstrip()
    os_line += f" ({_v(os_info.get('family'))} {_v(os_info.get('arch'))}"
    if os_info.get("kernel"):
        os_line += f", kernel {os_info['kernel']}"
    if os_info.get("build"):
        os_line += f", build {os_info['build']}"
    os_line += ")"

    lines = [
        os_line,
        f"CPU: {_v(cpu.get('model'))} ({_v(cpu.get('physical_cores'))} cores / "
        f"{_v(cpu.get('logical_cores'))} threads)",
        f"RAM: {_v(mem.get('total_gb'))} GB total, {_v(mem.get('available_gb'))} GB available",
        "GPU: " + ("; ".join(_describe_gpu(g) for g in gpus) if gpus else "none detected"),
        f"Disk: system drive {_v(sys_drive.get('free_gb'))} GB free of {_v(sys_drive.get('total_gb'))} GB; "
        f"temp location {_v(temp.get('free_gb'))} GB free",
        f"Running as administrator/root: {_yes_no(software.get('is_admin'))}",
        "Package managers: " + (", ".join(software.get("package_managers") or []) or "none found"),
    ]

    network = profile.get("network")
    if network is None:
        lines.append("Internet (model hosts reachable): not checked")
    else:
        reachable = any((network.get("model_hosts_reachable") or {}).values())
        lines.append(
            "Internet (model hosts reachable): "
            + ("yes" if reachable else "no (offline, or behind a proxy)")
        )

    if profile.get("probe_errors"):
        lines.append("Could not determine: " + ", ".join(profile["probe_errors"]))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print a hardware/software profile of this PC.")
    parser.add_argument("--json", action="store_true", help="print the full profile as JSON")
    parser.add_argument("--no-network", action="store_true", help="skip the internet reachability check")
    args = parser.parse_args()

    profile = collect_profile(check_network=not args.no_network)
    print(json.dumps(profile, indent=2) if args.json else summarize(profile))


if __name__ == "__main__":
    main()
