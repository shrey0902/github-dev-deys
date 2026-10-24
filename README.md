# github-dev-deys

## Update PHP and Node.js runtime packages

Run `scripts\Update-RuntimePackages.ps1` with PowerShell 7 on Windows Server or Linux. The script detects the operating system and updates installed PHP and Node.js packages through apt, dnf, yum, zypper, apk, winget, or Chocolatey. It requires an elevated PowerShell session on Windows or root privileges on Linux.

Use `-WhatIf` to preview the package upgrades without applying them:

```powershell
pwsh -File .\scripts\Update-RuntimePackages.ps1 -WhatIf
```

Only packages managed by the detected package manager are updated. Installations managed by nvm, source builds, or other custom methods are left untouched. The package manager's configured repositories determine which patched versions are available; review the output and verify versions after the update.