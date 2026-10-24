#!/usr/bin/env python3
"""Upgrade installed PHP and Node.js packages using native package managers."""

from __future__ import annotations

import argparse
import csv
import ctypes
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_NAME = "RuntimePackageUpdater"
CSV_FIELDS = (
    "timestamp",
    "event",
    "level",
    "operating_system",
    "package_manager",
    "package",
    "command",
    "status",
    "exit_code",
    "details",
)


class UpdateError(RuntimeError):
    pass


class AuditTrail:
    def __init__(self, log_dir: Path, operating_system: str) -> None:
        self.log_dir = log_dir
        self.operating_system = operating_system
        self.log_path = log_dir / "runtime-package-updater.log"
        self.csv_path = log_dir / "runtime-package-updater.csv"
        try:
            is_new_directory = not log_dir.exists()
            log_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
            if is_new_directory and operating_system == "Linux":
                log_dir.chmod(0o750)
            if not self.csv_path.exists():
                with self.csv_path.open("w", newline="", encoding="utf-8") as output:
                    csv.DictWriter(output, fieldnames=CSV_FIELDS).writeheader()
            if operating_system == "Linux":
                self.csv_path.chmod(0o640)
        except OSError as error:
            raise UpdateError(f"Cannot initialize audit files in {log_dir}: {error}") from error

    def record(
        self,
        event: str,
        *,
        level: str = "INFO",
        package_manager: str = "",
        package: str = "",
        command: list[str] | None = None,
        status: str = "",
        exit_code: int | None = None,
        details: str = "",
        write_native: bool = True,
    ) -> None:
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "level": level,
            "operating_system": self.operating_system,
            "package_manager": package_manager,
            "package": package,
            "command": json.dumps(command or [], ensure_ascii=True),
            "status": status,
            "exit_code": "" if exit_code is None else exit_code,
            "details": details,
        }
        try:
            with self.log_path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(entry, ensure_ascii=True) + "\n")
            with self.csv_path.open("a", newline="", encoding="utf-8") as output:
                csv.DictWriter(output, fieldnames=CSV_FIELDS).writerow(entry)
            if self.operating_system == "Linux":
                self.log_path.chmod(0o640)
        except OSError as error:
            raise UpdateError(f"Cannot write audit files in {self.log_dir}: {error}") from error

        if write_native:
            try:
                self._write_native_event(entry)
            except (OSError, UpdateError) as error:
                message = f"Native audit sink failed: {error}"
                self.record(
                    "audit_sink_error",
                    level="ERROR",
                    status="failed",
                    details=message,
                    write_native=False,
                )
                raise UpdateError(message) from error

    def _write_native_event(self, entry: dict[str, Any]) -> None:
        message = json.dumps(entry, ensure_ascii=True)
        if self.operating_system == "Windows":
            eventcreate = shutil.which("eventcreate.exe") or shutil.which("eventcreate")
            if not eventcreate:
                raise UpdateError("eventcreate was not found; Windows Event Log audit is unavailable.")
            event_type = "ERROR" if entry["level"] == "ERROR" else "WARNING" if entry["level"] == "WARNING" else "INFORMATION"
            event_id = "300" if event_type == "ERROR" else "200" if event_type == "WARNING" else "100"
            result = subprocess.run(
                [
                    eventcreate,
                    "/L",
                    "APPLICATION",
                    "/T",
                    event_type,
                    "/ID",
                    event_id,
                    "/SO",
                    APP_NAME,
                    "/D",
                    message[:30000],
                ],
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
        else:
            logger = shutil.which("logger")
            if not logger:
                raise UpdateError("logger was not found; Linux journal audit is unavailable.")
            priority = "user.err" if entry["level"] == "ERROR" else "user.warning" if entry["level"] == "WARNING" else "user.info"
            result = subprocess.run(
                [logger, "-t", APP_NAME, "-p", priority, message],
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
        if result.returncode != 0:
            error = result.stderr.strip() or f"exit code {result.returncode}"
            raise UpdateError(error)


def run_command(
    command: list[str],
    audit: AuditTrail,
    *,
    package_manager: str,
    event: str,
    package: str = "",
    what_if: bool = False,
) -> subprocess.CompletedProcess[str] | None:
    audit.record(
        f"{event}_planned" if what_if else f"{event}_started",
        package_manager=package_manager,
        package=package,
        command=command,
        status="what_if" if what_if else "started",
    )
    if what_if:
        return None

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        audit.record(
            f"{event}_failed",
            level="ERROR",
            package_manager=package_manager,
            package=package,
            command=command,
            status="failed",
            details=str(error),
        )
        raise UpdateError(f"Could not run {command[0]}: {error}") from error

    details = result.stderr.strip()[-2000:] if result.returncode else result.stdout.strip()[-2000:]
    audit.record(
        f"{event}_{'completed' if result.returncode == 0 else 'failed'}",
        level="INFO" if result.returncode == 0 else "ERROR",
        package_manager=package_manager,
        package=package,
        command=command,
        status="succeeded" if result.returncode == 0 else "failed",
        exit_code=result.returncode,
        details=details,
    )
    if result.returncode:
        raise UpdateError(f"Command failed with exit code {result.returncode}: {command[0]}")
    return result


def is_runtime_package(package: str) -> bool:
    name = package.lower().split(":", maxsplit=1)[0]
    return (
        re.fullmatch(r"php(?:[0-9].*|-.+)?", name) is not None
        or re.fullmatch(r"libapache2-mod-php(?:[0-9].*)?", name) is not None
        or name == "nodejs"
        or name.startswith("nodejs-")
    )


def linux_packages(package_manager: str, audit: AuditTrail) -> list[str]:
    if package_manager == "apt-get":
        command = [
            "dpkg-query",
            "--show",
            "--showformat=${binary:Package}\\t${db:Status-Abbrev}\\n",
        ]
    elif package_manager in {"dnf", "yum", "zypper"}:
        command = ["rpm", "-qa", "--qf", "%{NAME}\\n"]
    else:
        command = ["apk", "info"]

    result = run_command(
        command,
        audit,
        package_manager=package_manager,
        event="package_inventory",
    )
    if result is None:
        raise UpdateError("Package inventory unexpectedly skipped.")
    packages = []
    for line in result.stdout.splitlines():
        if package_manager == "apt-get":
            fields = line.split("\t", maxsplit=1)
            if len(fields) != 2 or not fields[1].startswith("ii"):
                continue
            line = fields[0]
        if is_runtime_package(line):
            packages.append(line)
    return sorted(set(packages))


def winget_packages(audit: AuditTrail) -> list[str]:
    command = [
        "winget",
        "list",
        "--accept-source-agreements",
        "--disable-interactivity",
    ]
    result = run_command(command, audit, package_manager="winget", event="package_inventory")
    if result is None:
        raise UpdateError("Package inventory unexpectedly skipped.")
    package_ids = set(
        re.findall(
            r"(?i)(?<!\S)(PHP\.PHP(?:\.[A-Z0-9_-]+)?|OpenJS\.NodeJS(?:\.LTS)?)(?=\s)",
            result.stdout,
        )
    )
    return sorted(package_ids)


def chocolatey_packages(audit: AuditTrail) -> list[str]:
    command = ["choco", "list", "--local-only", "--limit-output"]
    result = run_command(command, audit, package_manager="choco", event="package_inventory")
    if result is None:
        raise UpdateError("Package inventory unexpectedly skipped.")
    package_names = []
    for line in result.stdout.splitlines():
        name = line.partition("|")[0]
        if re.fullmatch(r"(?i)(php(?:[.-].*|[0-9].*)?|nodejs(?:[.-].*)?)", name):
            package_names.append(name)
    return sorted(set(package_names))


def available_managers(operating_system: str) -> list[str]:
    if operating_system == "Windows":
        return [name for name in ("winget", "choco") if shutil.which(name)]
    for name in ("apt-get", "dnf", "yum", "zypper", "apk"):
        if shutil.which(name):
            return [name]
    return []


def assert_elevated(operating_system: str) -> None:
    if operating_system == "Windows":
        try:
            elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError) as error:
            raise UpdateError(f"Could not verify administrator privileges: {error}") from error
    else:
        elevated = os.geteuid() == 0
    if not elevated:
        role = "an elevated PowerShell/Command Prompt session" if operating_system == "Windows" else "root (for example, with sudo)"
        raise UpdateError(f"Run this script as {role}.")


def default_log_dir(operating_system: str) -> Path:
    if operating_system == "Windows":
        program_data = os.environ.get("ProgramData", r"C:\ProgramData")
        return Path(program_data) / APP_NAME / "logs"
    return Path("/var/log/runtime-package-updater")


def update_package(
    package_manager: str,
    package: str,
    audit: AuditTrail,
    what_if: bool,
) -> None:
    if package_manager == "winget":
        command = [
            "winget",
            "upgrade",
            "--id",
            package,
            "--exact",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--disable-interactivity",
        ]
    elif package_manager == "choco":
        command = ["choco", "upgrade", package, "--yes", "--no-progress"]
    elif package_manager == "apt-get":
        command = ["apt-get", "install", "--only-upgrade", "-y", package]
    elif package_manager in {"dnf", "yum"}:
        command = [package_manager, "upgrade", "-y", package]
    elif package_manager == "zypper":
        command = ["zypper", "update", "-y", package]
    else:
        command = ["apk", "upgrade", package]
    run_command(
        command,
        audit,
        package_manager=package_manager,
        package=package,
        event="package_upgrade",
        what_if=what_if,
    )


def refresh_metadata(package_manager: str, audit: AuditTrail, what_if: bool) -> None:
    commands = {
        "apt-get": ["apt-get", "update"],
        "zypper": ["zypper", "refresh"],
        "apk": ["apk", "update"],
    }
    command = commands.get(package_manager)
    if command:
        run_command(
            command,
            audit,
            package_manager=package_manager,
            event="repository_refresh",
            what_if=what_if,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upgrade installed PHP and Node.js packages through native package managers."
    )
    parser.add_argument(
        "--what-if",
        action="store_true",
        help="Discover packages and audit planned commands without applying updates.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        help="Directory for the JSON-lines log and CSV audit trail.",
    )
    args = parser.parse_args()
    operating_system = platform.system()
    if operating_system not in {"Windows", "Linux"}:
        parser.error("This script supports Windows and Linux only.")

    audit: AuditTrail | None = None
    try:
        audit = AuditTrail(args.log_dir or default_log_dir(operating_system), operating_system)
        audit.record(
            "run_started",
            status="what_if" if args.what_if else "started",
            details=f"Python {platform.python_version()}",
        )
        assert_elevated(operating_system)
        managers = available_managers(operating_system)
        if not managers:
            raise UpdateError("No supported native package manager was found.")
        audit.record("package_managers_detected", details=", ".join(managers))

        package_count = 0
        for manager in managers:
            if manager == "winget":
                packages = winget_packages(audit)
            elif manager == "choco":
                packages = chocolatey_packages(audit)
            else:
                if manager == "apt-get" and not shutil.which("dpkg-query"):
                    raise UpdateError("apt-get was found, but dpkg-query is unavailable.")
                if manager in {"dnf", "yum", "zypper"} and not shutil.which("rpm"):
                    raise UpdateError(f"{manager} was found, but rpm is unavailable.")
                packages = linux_packages(manager, audit)
            audit.record(
                "runtime_packages_detected",
                package_manager=manager,
                status="found" if packages else "none_found",
                details=", ".join(packages) if packages else "No installed PHP or Node.js packages found.",
            )
            if packages:
                refresh_metadata(manager, audit, args.what_if)
            for package in packages:
                update_package(manager, package, audit, args.what_if)
                package_count += 1

        audit.record(
            "run_completed",
            status="what_if" if args.what_if else "succeeded",
            details=f"{'Would update' if args.what_if else 'Processed'} {package_count} package(s).",
        )
        return 0
    except (UpdateError, OSError) as error:
        if audit is not None:
            try:
                audit.record(
                    "run_failed",
                    level="ERROR",
                    status="failed",
                    details=str(error),
                    write_native=False,
                )
            except UpdateError as audit_error:
                print(f"ERROR: Could not write failure to audit files: {audit_error}", file=sys.stderr)
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
