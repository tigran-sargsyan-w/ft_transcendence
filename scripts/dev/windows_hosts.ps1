
param(
    [ValidateSet("check", "plan", "setup", "_apply")]
    [string]$Action = "check",

    [string]$Domain = "transcendence.test",

    [switch]$Apply
)

$ErrorActionPreference = "Stop"

$Domain = $Domain.Trim().ToLowerInvariant()
$ExpectedIP = "127.0.0.1"
$Marker = "# ft_transcendence:managed"

$HostsFile = Join-Path $env:SystemRoot `
    "System32\drivers\etc\hosts"

$DomainPattern = '^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+test$'

if ($Domain -notmatch $DomainPattern) {
    Write-Host "[ERROR] Invalid .test domain"
    exit 2
}


function Test-Administrator {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()

    $Principal = New-Object `
        Security.Principal.WindowsPrincipal($Identity)

    return $Principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}


function Get-DomainAddresses {
    param(
        [string]$Content,
        [string]$Name
    )

    foreach ($Line in ($Content -split '\r\n|\n|\r')) {
        $Text = ($Line -split '#', 2)[0].Trim()

        if (-not $Text) {
            continue
        }

        $Fields = $Text -split '\s+'

        if ($Fields.Count -lt 2) {
            continue
        }

        $Aliases = @(
            $Fields[1..($Fields.Count - 1)] |
                ForEach-Object { $_.ToLowerInvariant() }
        )

        if ($Aliases -contains $Name) {
            $Fields[0]
        }
    }
}


function Get-CurrentAddresses {
    $Content = [IO.File]::ReadAllText($HostsFile)

    return @(
        Get-DomainAddresses -Content $Content -Name $Domain |
            Sort-Object -Unique
    )
}


function Add-ManagedMapping {
    if (-not (Test-Administrator)) {
        throw "Administrator privileges are required"
    }

    # Open with an exclusive writer lock.
    $Stream = [IO.File]::Open(
        $HostsFile,
        [IO.FileMode]::Open,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::Read
    )

    try {
        $Reader = [IO.StreamReader]::new(
            $Stream,
            [Text.Encoding]::UTF8,
            $true,
            1024,
            $true
        )

        try {
            $Content = $Reader.ReadToEnd()
        }
        finally {
            $Reader.Dispose()
        }

        # Re-check while holding the file open.
        $Addresses = @(
            Get-DomainAddresses -Content $Content -Name $Domain |
                Sort-Object -Unique
        )

        if (
            $Addresses.Count -eq 1 -and
            $Addresses[0] -eq $ExpectedIP
        ) {
            Write-Host "[OK] Domain already configured"
            return 0
        }

        if ($Addresses.Count -gt 0) {
            throw "Conflicting mapping: $($Addresses -join ', ')"
        }

        $LastByte = -1

        if ($Stream.Length -gt 0) {
            [void]$Stream.Seek(-1, [IO.SeekOrigin]::End)
            $LastByte = $Stream.ReadByte()
        }

        [void]$Stream.Seek(0, [IO.SeekOrigin]::End)

        $Prefix = ""

        if ($Stream.Length -gt 0 -and $LastByte -ne 10) {
            $Prefix = "`r`n"
        }

        $Entry = "$ExpectedIP`t$Domain`t$Marker"
        $Bytes = [Text.Encoding]::ASCII.GetBytes(
            "$Prefix$Entry`r`n"
        )

        $Stream.Write($Bytes, 0, $Bytes.Length)
        $Stream.Flush($true)
    }
    finally {
        $Stream.Dispose()
    }

    Write-Host "[OK] Added $Domain -> $ExpectedIP"
    return 0
}


try {
    if ($Action -eq "_apply") {
        exit (Add-ManagedMapping)
    }

    $Addresses = @(Get-CurrentAddresses)

    if (
        $Addresses.Count -eq 1 -and
        $Addresses[0] -eq $ExpectedIP
    ) {
        Write-Host "[OK] Windows: $Domain -> $ExpectedIP"
        exit 0
    }

    if ($Addresses.Count -gt 0) {
        Write-Host "[CONFLICT] Windows: $Domain -> $($Addresses -join ', ')"
        exit 2
    }

    Write-Host "[MISSING] Windows: $Domain"

    if ($Action -eq "check") {
        exit 1
    }

    Write-Host "[PLAN] Add to Windows hosts:"
    Write-Host "$ExpectedIP`t$Domain`t$Marker"

    if ($Action -eq "plan") {
        exit 1
    }

    if (-not $Apply) {
        Write-Host "[INFO] Re-run setup with -Apply to authorize the change"
        exit 1
    }

    if (Test-Administrator) {
        exit (Add-ManagedMapping)
    }

    Write-Host "[UAC] Requesting administrator permission..."

    $Arguments = (
        '-NoProfile -NonInteractive -ExecutionPolicy Bypass ' +
        '-File "{0}" -Action _apply -Domain {1}'
    ) -f $PSCommandPath, $Domain

    $Process = Start-Process `
        -FilePath (Join-Path $PSHOME "powershell.exe") `
        -ArgumentList $Arguments `
        -Verb RunAs `
        -Wait `
        -PassThru

    exit $Process.ExitCode
}
catch {
    Write-Host "[ERROR] $($_.Exception.Message)"
    exit 2
}
