import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update_runtime_packages as updater


class RuntimePackageUpdaterTests(unittest.TestCase):
    def test_runtime_package_matching(self):
        self.assertTrue(updater.is_runtime_package("php8.3-cli"))
        self.assertTrue(updater.is_runtime_package("libapache2-mod-php8.3"))
        self.assertTrue(updater.is_runtime_package("nodejs:amd64"))
        self.assertFalse(updater.is_runtime_package("phpmyadmin"))
        self.assertFalse(updater.is_runtime_package("phpunit"))
        self.assertFalse(updater.is_runtime_package("nginx"))
        self.assertFalse(updater.is_runtime_package("node"))

    def test_apt_inventory_filters_uninstalled_and_unrelated_packages(self):
        audit = Mock()
        inventory = subprocess.CompletedProcess(
            args=["dpkg-query"],
            returncode=0,
            stdout="php8.3-cli\tii \nnodejs:amd64\tii \nnginx\tii \nphp8.2-fpm\trc \n",
            stderr="",
        )
        with patch.object(updater, "run_command", return_value=inventory):
            packages = updater.linux_packages("apt-get", audit)
        self.assertEqual(packages, ["nodejs:amd64", "php8.3-cli"])

    def test_audit_record_writes_json_lines_and_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = updater.AuditTrail(Path(directory), "Linux")
            with patch.object(audit, "_write_native_event") as native_event:
                audit.record(
                    "package_upgrade_completed",
                    package_manager="apt-get",
                    package="php8.3-cli",
                    command=["apt-get", "install", "--only-upgrade", "-y", "php8.3-cli"],
                    status="succeeded",
                    exit_code=0,
                )

            log_record = json.loads(audit.log_path.read_text(encoding="utf-8").splitlines()[0])
            with audit.csv_path.open(newline="", encoding="utf-8") as csv_file:
                csv_records = list(csv.DictReader(csv_file))
            self.assertEqual(log_record["event"], "package_upgrade_completed")
            self.assertEqual(csv_records[0]["package"], "php8.3-cli")
            self.assertEqual(csv_records[0]["status"], "succeeded")
            native_event.assert_called_once()

    def test_linux_native_event_uses_logger(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = updater.AuditTrail(Path(directory), "Linux")
            completed = subprocess.CompletedProcess(args=["logger"], returncode=0, stdout="", stderr="")
            with (
                patch.object(updater.shutil, "which", return_value="logger"),
                patch.object(updater.subprocess, "run", return_value=completed) as run,
            ):
                audit._write_native_event({"level": "INFO", "event": "run_started"})
        command = run.call_args.args[0]
        self.assertEqual(command[0], "logger")
        self.assertEqual(command[1:4], ["-t", updater.APP_NAME, "-p"])

    def test_windows_native_event_uses_eventcreate(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = updater.AuditTrail(Path(directory), "Windows")
            completed = subprocess.CompletedProcess(args=["eventcreate"], returncode=0, stdout="", stderr="")
            with (
                patch.object(updater.shutil, "which", return_value="eventcreate.exe"),
                patch.object(updater.subprocess, "run", return_value=completed) as run,
            ):
                audit._write_native_event({"level": "INFO", "event": "run_started"})
        command = run.call_args.args[0]
        self.assertEqual(command[0], "eventcreate.exe")
        self.assertIn("APPLICATION", command)
        self.assertIn(updater.APP_NAME, command)

    def test_what_if_records_plan_without_running_command(self):
        audit = Mock()
        with patch.object(updater.subprocess, "run") as run:
            result = updater.run_command(
                ["apt-get", "install", "--only-upgrade", "-y", "php8.3-cli"],
                audit,
                package_manager="apt-get",
                event="package_upgrade",
                package="php8.3-cli",
                what_if=True,
            )
        self.assertIsNone(result)
        run.assert_not_called()
        audit.record.assert_called_once()
        self.assertEqual(audit.record.call_args.kwargs["status"], "what_if")


if __name__ == "__main__":
    unittest.main()
