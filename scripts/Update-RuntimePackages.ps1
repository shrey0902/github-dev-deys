[CmdletBinding(SupportsShouldProcess = $true)]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-CommandAvailable {
    param([Parameter(Mandatory)][string]$Name)

    return $null -ne (Get-Command -Name $Name -ErrorAction SilentlyContinue)
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$Arguments
    )

    $output = & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE."
    }

    return $output
}

function Get-LinuxRuntimePackages {
    param([Parameter(Mandatory)][string]$PackageManager)

    if ($PackageManager -eq 'apt-get') {
        $packages = foreach ($line in Invoke-NativeCommand -FilePath 'dpkg-query' -Arguments @('--show', '--showformat=${binary:Package}\t${db:Status-Abbrev}\n')) {
            $fields = "$line" -split "`t"
            if ($fields.Count -ge 2 -and $fields[1].StartsWith('ii')) {
                $fields[0]
            }
        }
    } elseif ($PackageManager -in @('dnf', 'yum', 'zypper')) {
        $packages = Invoke-NativeCommand -FilePath 'rpm' -Arguments @('-qa', '--qf', '%{NAME}\n')
    } else {
        $packages = Invoke-NativeCommand -FilePath 'apk' -Arguments @('info')
    }

    return @(
        $packages |
            Where-Object {
                $_ -match '^(php(?:[0-9].*|-.+)?|libapache2-mod-php(?:[0-9].*)?)(?::[a-z0-9-]+)?$' -or
                $_ -match '^nodejs(?:-.+)?(?::[a-z0-9-]+)?$'
            } |
            Sort-Object -Unique
    )
}

function Get-ChocolateyRuntimePackages {
    $packages = foreach ($line in Invoke-NativeCommand -FilePath 'choco' -Arguments @('list', '--local-only', '--limit-output')) {
        if ("$line" -match '^([^|]+)\|') {
            $name = $Matches[1]
            if ($name -match '(?i)^(php(?:[.-].*|[0-9].*)?|nodejs(?:[.-].*)?)$') {
                $name
            }
        }
    }

    return @($packages | Sort-Object -Unique)
}

function Get-WingetRuntimePackages {
    $packages = foreach ($line in Invoke-NativeCommand -FilePath 'winget' -Arguments @('list', '--accept-source-agreements', '--disable-interactivity')) {
        if ("$line" -match '(?i)(?<!\S)(?<id>PHP\.PHP(?:\.[A-Z0-9_-]+)?|OpenJS\.NodeJS(?:\.LTS)?)(?=\s)') {
            $Matches['id']
        }
    }

    return @($packages | Sort-Object -Unique)
}

function Assert-Administrator {
    if ($IsWindows) {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = [Security.Principal.WindowsPrincipal]::new($identity)
        if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            throw 'Run this script from an elevated PowerShell session.'
        }
    } elseif ([Environment]::UserName -ne 'root') {
        throw 'Run this script as root (for example, with sudo).'
    }
}

if (-not $IsWindows -and -not $IsLinux) {
    throw 'This script supports Windows and Linux only.'
}

Assert-Administrator

if ($IsWindows) {
    $managers = @()
    if (Test-CommandAvailable -Name 'winget') {
        $managers += 'winget'
    }
    if (Test-CommandAvailable -Name 'choco') {
        $managers += 'choco'
    }

    if ($managers.Count -eq 0) {
        throw 'No supported Windows package manager was found. Install winget or Chocolatey and manage PHP/Node with it.'
    }

    foreach ($manager in $managers) {
        if ($manager -eq 'winget') {
            $packages = @(Get-WingetRuntimePackages)
        } else {
            $packages = @(Get-ChocolateyRuntimePackages)
        }

        if ($packages.Count -eq 0) {
            Write-Output "No installed PHP or Node packages were found through $manager."
        }

        foreach ($package in $packages) {
            if ($manager -eq 'winget') {
                $arguments = @('upgrade', '--id', $package, '--exact', '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity')
            } else {
                $arguments = @('upgrade', $package, '--yes', '--no-progress')
            }

            if ($PSCmdlet.ShouldProcess("$manager package $package", 'Upgrade')) {
                Invoke-NativeCommand -FilePath $manager -Arguments $arguments | Out-Host
            }
        }
    }
} else {
    $packageManager = @('apt-get', 'dnf', 'yum', 'zypper', 'apk') |
        Where-Object { Test-CommandAvailable -Name $_ } |
        Select-Object -First 1

    if (-not $packageManager) {
        throw 'No supported Linux package manager was found (apt-get, dnf, yum, zypper, or apk).'
    }

    if ($packageManager -eq 'apt-get' -and -not (Test-CommandAvailable -Name 'dpkg-query')) {
        throw 'apt-get was found, but dpkg-query is unavailable.'
    }
    if ($packageManager -in @('dnf', 'yum', 'zypper') -and -not (Test-CommandAvailable -Name 'rpm')) {
        throw "$packageManager was found, but rpm is unavailable."
    }

    $packages = @(Get-LinuxRuntimePackages -PackageManager $packageManager)
    if ($packages.Count -eq 0) {
        Write-Output "No installed PHP or Node packages were found through $packageManager."
        return
    }

    if ($packageManager -eq 'apt-get') {
        $updateArguments = @('update')
        $upgradeArguments = @('install', '--only-upgrade', '-y') + $packages
    } elseif ($packageManager -in @('dnf', 'yum')) {
        $updateArguments = $null
        $upgradeArguments = @('upgrade', '-y') + $packages
    } elseif ($packageManager -eq 'zypper') {
        $updateArguments = @('refresh')
        $upgradeArguments = @('update', '-y') + $packages
    } else {
        $updateArguments = @('update')
        $upgradeArguments = @('upgrade') + $packages
    }

    if ($null -ne $updateArguments -and $PSCmdlet.ShouldProcess("$packageManager repositories", 'Refresh package metadata')) {
        Invoke-NativeCommand -FilePath $packageManager -Arguments $updateArguments | Out-Host
    }
    if ($PSCmdlet.ShouldProcess(($packages -join ', '), 'Upgrade installed PHP and Node packages')) {
        Invoke-NativeCommand -FilePath $packageManager -Arguments $upgradeArguments | Out-Host
    }
}
