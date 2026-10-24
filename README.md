# github-dev-deys

## Update PHP and Node.js runtime packages

Run `scripts\update_runtime_packages.py` with Python 3.10 or later on Windows Server or Linux. The script detects the operating system and updates installed PHP and Node.js packages through apt, dnf, yum, zypper, apk, winget, or Chocolatey. It requires an elevated session on Windows or root privileges on Linux.

Use `--what-if` to preview planned package upgrades without applying them:

```text
python scripts\update_runtime_packages.py --what-if
```

The script writes a JSON-lines log and CSV audit file to `C:\ProgramData\RuntimePackageUpdater\logs` on Windows and `/var/log/runtime-package-updater` on Linux by default. It also writes each audit event to the Windows Application Event Log or Linux system journal (through `logger`). Use `--log-dir` to choose a different file-log directory. If the native event/journal sink fails, the script records the failure in the files and stops further package operations.

Only packages managed by the detected package manager are updated. Installations managed by nvm, source builds, or other custom methods are left untouched. The package manager's configured repositories determine which patched versions are available; review the output and verify versions after the update.