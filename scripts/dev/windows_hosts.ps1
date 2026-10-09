
param(
    [ValidateSet("check", "plan")]
    [string]$Action = "check",

    [string]$Domain = "transcendence.test"
)

$ErrorActionPreference = "Stop"

$Domain = $Domain.Trim().ToLowerInvariant()
$ExpectedIP = "127.0.0.1"
$Marker = "# ft_transcendence:managed"

$HostsFile = Join-Path $env:SystemRoot `
    "System32\drivers\etc\hosts"

# Validate the local domain before processing.
$DomainPattern = '^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+test$'

if ($Domain -notmatch $DomainPattern) {
    Write-Host "[ERROR] Invalid .test domain"
    exit 2
}

function Get-DomainAddresses {
    param(
        [string]$Path,
        [string]$Name
    )

    $Addresses = @()

    foreach ($Line in [System.IO.File]::ReadAllLines($Path)) {
        $Content = ($Line -split '#', 2)[0].Trim()

        if (-not $Content) {
            continue
        }

        $Fields = $Content -split '\s+'

        if ($Fields.Count -lt 2) {
            continue
        }

        $Aliases = @(
            $Fields[1..($Fields.Count - 1)] |
                ForEach-Object { $_.ToLowerInvariant() }
        )

        if ($Aliases -contains $Name) {
            $Addresses += $Fields[0]
        }
    }

    return $Addresses
}

try {
    $Addresses = @(
        Get-DomainAddresses -Path $HostsFile -Name $Domain |
            Sort-Object -Unique
    )

    if ($Addresses.Count -eq 0) {
        Write-Host "[MISSING] Windows: $Domain"

        if ($Action -eq "plan") {
            Write-Host "[PLAN] Add to Windows hosts:"
            Write-Host "$ExpectedIP`t$Domain`t$Marker"
        }

        exit 1
    }

    if (
        $Addresses.Count -eq 1 -and
        $Addresses[0] -eq $ExpectedIP
    ) {
        Write-Host "[OK] Windows: $Domain -> $ExpectedIP"
        exit 0
    }

    Write-Host "[CONFLICT] Windows: $Domain -> $($Addresses -join ', ')"
    exit 2
}
catch {
    Write-Host "[ERROR] $($_.Exception.Message)"
    exit 2
}
