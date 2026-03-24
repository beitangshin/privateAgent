from __future__ import annotations

import json
import locale
import platform
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import ToolContext, ToolError, ToolInputModel, ToolSpec


def _resolve_allowed_path(raw_path: str, allowed_roots: tuple[Path, ...]) -> Path:
    candidate = Path(raw_path).expanduser().resolve()
    for root in allowed_roots:
        try:
            candidate.relative_to(root)
            return candidate
        except ValueError:
            continue
    raise ToolError(f"path '{candidate}' is outside allowed roots")


def _run_powershell_json(command: str, *, timeout_sec: int = 15) -> Any:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding=locale.getpreferredencoding(False),
        errors="replace",
        timeout=timeout_sec,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip() or "powershell command failed"
        raise ToolError(stderr)
    output = completed.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise ToolError("powershell command returned invalid JSON") from exc


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


@dataclass(slots=True)
class PingInput(ToolInputModel):
    pass


def ping(_: PingInput, __: ToolContext) -> dict[str, Any]:
    return {"ok": True, "message": "pong"}


@dataclass(slots=True)
class DesktopStatusInput(ToolInputModel):
    pass


def summarize_desktop_status(_: DesktopStatusInput, context: ToolContext) -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "safe_mode": context.safe_mode,
        "model_backend": context.model_backend_name,
        "allowed_roots": [str(path) for path in context.allowed_roots],
        "notes_dir": str(context.notes_dir),
        "network_tools_enabled": context.enable_network_tools,
        "desktop_tools_enabled": context.enable_desktop_tools,
    }


@dataclass(slots=True)
class ReadAllowedFileInput(ToolInputModel):
    path: str
    max_chars: int = 4000

    def __post_init__(self) -> None:
        if not self.path:
            raise ToolError("path is required")
        if not 1 <= self.max_chars <= 20000:
            raise ToolError("max_chars must be between 1 and 20000")


def read_allowed_file(data: ReadAllowedFileInput, context: ToolContext) -> dict[str, Any]:
    path = _resolve_allowed_path(data.path, context.allowed_roots)
    content = path.read_text(encoding="utf-8")
    return {
        "path": str(path),
        "content": content[: data.max_chars],
        "truncated": len(content) > data.max_chars,
    }


@dataclass(slots=True)
class ListAllowedDirectoryInput(ToolInputModel):
    path: str

    def __post_init__(self) -> None:
        if not self.path:
            raise ToolError("path is required")


def list_allowed_directory(
    data: ListAllowedDirectoryInput, context: ToolContext
) -> dict[str, Any]:
    path = _resolve_allowed_path(data.path, context.allowed_roots)
    if not path.is_dir():
        raise ToolError(f"path '{path}' is not a directory")
    entries = []
    for item in sorted(path.iterdir(), key=lambda entry: entry.name.lower()):
        entries.append(
            {
                "name": item.name,
                "is_dir": item.is_dir(),
                "size": item.stat().st_size if item.is_file() else None,
            }
        )
    return {"path": str(path), "entries": entries}


@dataclass(slots=True)
class CaptureSystemInfoInput(ToolInputModel):
    pass


def capture_system_info(_: CaptureSystemInfoInput, __: ToolContext) -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@dataclass(slots=True)
class SystemHealthInput(ToolInputModel):
    pass


def get_system_health(_: SystemHealthInput, context: ToolContext) -> dict[str, Any]:
    command = """
$os = Get-CimInstance Win32_OperatingSystem
$cpuLoads = @(Get-CimInstance Win32_Processor | Select-Object -ExpandProperty LoadPercentage)
$systemDrive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$env:SystemDrive'"
$boot = [datetime]$os.LastBootUpTime.DateTime
[pscustomobject]@{
  computer_name = $env:COMPUTERNAME
  last_boot_utc = $boot.ToUniversalTime().ToString("o")
  uptime_hours = [math]::Round(((Get-Date) - $boot).TotalHours, 2)
  cpu_load_percent = if ($cpuLoads.Count -gt 0) { [math]::Round(($cpuLoads | Measure-Object -Average).Average, 2) } else { $null }
  memory_total_gb = [math]::Round(([double]$os.TotalVisibleMemorySize * 1KB) / 1GB, 2)
  memory_free_gb = [math]::Round(([double]$os.FreePhysicalMemory * 1KB) / 1GB, 2)
  system_drive = $env:SystemDrive
  system_drive_total_gb = if ($systemDrive.Size) { [math]::Round(([double]$systemDrive.Size) / 1GB, 2) } else { $null }
  system_drive_free_gb = if ($systemDrive.FreeSpace) { [math]::Round(([double]$systemDrive.FreeSpace) / 1GB, 2) } else { $null }
} | ConvertTo-Json -Compress
""".strip()
    data = _run_powershell_json(command)
    if not isinstance(data, dict):
        raise ToolError("system health query returned unexpected data")
    data["safe_mode"] = context.safe_mode
    data["model_backend"] = context.model_backend_name
    return data


@dataclass(slots=True)
class DiskUsageInput(ToolInputModel):
    pass


def get_disk_usage(_: DiskUsageInput, __: ToolContext) -> dict[str, Any]:
    command = """
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
Sort-Object DeviceID |
ForEach-Object {
  [pscustomobject]@{
    device_id = $_.DeviceID
    volume_name = $_.VolumeName
    size_bytes = [int64]$_.Size
    free_bytes = [int64]$_.FreeSpace
    free_percent = if ($_.Size) { [math]::Round(([double]$_.FreeSpace / [double]$_.Size) * 100, 2) } else { $null }
  }
} | ConvertTo-Json -Compress
""".strip()
    disks = _ensure_list(_run_powershell_json(command))
    return {"disks": disks}


@dataclass(slots=True)
class TopProcessesInput(ToolInputModel):
    limit: int = 8

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 20:
            raise ToolError("limit must be between 1 and 20")


def get_top_processes(data: TopProcessesInput, __: ToolContext) -> dict[str, Any]:
    command = f"""
Get-Process |
Sort-Object WorkingSet64 -Descending |
Select-Object -First {data.limit} |
ForEach-Object {{
  [pscustomobject]@{{
    name = $_.ProcessName
    pid = $_.Id
    cpu_seconds = if ($_.CPU -ne $null) {{ [math]::Round([double]$_.CPU, 2) }} else {{ $null }}
    working_set_mb = [math]::Round(([double]$_.WorkingSet64) / 1MB, 2)
    private_memory_mb = [math]::Round(([double]$_.PM) / 1MB, 2)
  }}
}} | ConvertTo-Json -Compress
""".strip()
    processes = _ensure_list(_run_powershell_json(command))
    return {"limit": data.limit, "processes": processes}


@dataclass(slots=True)
class NetworkSummaryInput(ToolInputModel):
    pass


def get_network_summary(_: NetworkSummaryInput, context: ToolContext) -> dict[str, Any]:
    if not context.enable_network_tools:
        raise ToolError("network tools are disabled by configuration")
    command = """
Get-NetIPConfiguration |
Where-Object { $_.NetAdapter.Status -eq 'Up' } |
ForEach-Object {
  [pscustomobject]@{
    interface_alias = $_.InterfaceAlias
    interface_description = $_.InterfaceDescription
    ipv4_addresses = @($_.IPv4Address | ForEach-Object { $_.IPAddress })
    ipv6_addresses = @($_.IPv6Address | ForEach-Object { $_.IPAddress })
    default_gateways = @($_.IPv4DefaultGateway | ForEach-Object { $_.NextHop })
    dns_servers = @($_.DNSServer.ServerAddresses)
  }
} | ConvertTo-Json -Compress
""".strip()
    adapters = _ensure_list(_run_powershell_json(command))
    return {
        "host": platform.node(),
        "network_tools_enabled": context.enable_network_tools,
        "adapters": adapters,
    }


@dataclass(slots=True)
class TakeNoteInput(ToolInputModel):
    title: str
    body: str

    def __post_init__(self) -> None:
        if not self.title or len(self.title) > 120:
            raise ToolError("title must be between 1 and 120 chars")
        if not self.body or len(self.body) > 20000:
            raise ToolError("body must be between 1 and 20000 chars")


def take_note(data: TakeNoteInput, context: ToolContext) -> dict[str, Any]:
    context.notes_dir.mkdir(parents=True, exist_ok=True)
    safe_title = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in data.title)
    filename = f"{safe_title}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.md"
    note_path = context.notes_dir / filename
    note_path.write_text(data.body, encoding="utf-8")
    return {"path": str(note_path), "bytes_written": note_path.stat().st_size}


def build_builtin_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="ping",
            description="Check that the assistant is responsive.",
            category="info",
            risk_level="low",
            side_effects=False,
            requires_confirmation=False,
            timeout_sec=5,
            input_model=PingInput,
            handler=ping,
        ),
        ToolSpec(
            name="summarize_desktop_status",
            description="Return a safe summary of the current desktop host state.",
            category="system",
            risk_level="low",
            side_effects=False,
            requires_confirmation=False,
            timeout_sec=5,
            input_model=DesktopStatusInput,
            handler=summarize_desktop_status,
        ),
        ToolSpec(
            name="read_allowed_file",
            description="Read a UTF-8 text file under an allowlisted root.",
            category="filesystem_read",
            risk_level="low",
            side_effects=False,
            requires_confirmation=False,
            timeout_sec=10,
            input_model=ReadAllowedFileInput,
            handler=read_allowed_file,
        ),
        ToolSpec(
            name="list_allowed_directory",
            description="List a directory under an allowlisted root.",
            category="filesystem_read",
            risk_level="low",
            side_effects=False,
            requires_confirmation=False,
            timeout_sec=10,
            input_model=ListAllowedDirectoryInput,
            handler=list_allowed_directory,
        ),
        ToolSpec(
            name="capture_system_info",
            description="Return safe host metadata for diagnostics.",
            category="system",
            risk_level="low",
            side_effects=False,
            requires_confirmation=False,
            timeout_sec=5,
            input_model=CaptureSystemInfoInput,
            handler=capture_system_info,
        ),
        ToolSpec(
            name="get_system_health",
            description="Return CPU, memory, uptime, and system drive health.",
            category="system",
            risk_level="low",
            side_effects=False,
            requires_confirmation=False,
            timeout_sec=15,
            input_model=SystemHealthInput,
            handler=get_system_health,
        ),
        ToolSpec(
            name="get_disk_usage",
            description="Return local disk usage for fixed drives.",
            category="system",
            risk_level="low",
            side_effects=False,
            requires_confirmation=False,
            timeout_sec=15,
            input_model=DiskUsageInput,
            handler=get_disk_usage,
        ),
        ToolSpec(
            name="get_top_processes",
            description="Return the top local processes by memory usage.",
            category="system",
            risk_level="low",
            side_effects=False,
            requires_confirmation=False,
            timeout_sec=15,
            input_model=TopProcessesInput,
            handler=get_top_processes,
        ),
        ToolSpec(
            name="get_network_summary",
            description="Return a safe summary of active local network interfaces.",
            category="network",
            risk_level="low",
            side_effects=False,
            requires_confirmation=False,
            timeout_sec=15,
            input_model=NetworkSummaryInput,
            handler=get_network_summary,
        ),
        ToolSpec(
            name="take_note",
            description="Write a markdown note into the configured notes directory.",
            category="filesystem_write",
            risk_level="medium",
            side_effects=True,
            requires_confirmation=False,
            timeout_sec=10,
            input_model=TakeNoteInput,
            handler=take_note,
        ),
    ]
