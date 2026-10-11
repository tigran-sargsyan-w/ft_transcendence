
$ErrorActionPreference = "Stop"

. "$PSScriptRoot/../../scripts/dev/windows_hosts_core.ps1"

$Domain = "transcendence.test"
$Passed = 0

function Assert-Equal {
    param($Actual, $Expected, [string]$Name)

    if ($Actual -cne $Expected) {
        throw "FAILED: $Name"
    }

    $script:Passed++
    Write-Host "[PASS] $Name"
}

try {
    # 1. Remove a managed entry.
    $Original = "127.0.0.1 localhost`r`n" +
                "127.0.0.1 transcendence.test " +
                "# ft_transcendence:managed`r`n"

    $Result = Remove-ManagedMappingContent `
        -Content $Original -Domain $Domain

    Assert-Equal $Result.Content `
        "127.0.0.1 localhost`r`n" `
        "Remove managed entry"

    # 2. Preserve an unmanaged entry.
    $Original = "127.0.0.1 transcendence.test`r`n"

    $Result = Remove-ManagedMappingContent `
        -Content $Original -Domain $Domain

    Assert-Equal $Result.Content $Original `
        "Preserve unmanaged entry"

    # 3. Preserve shared aliases.
    $Original = (
        "127.0.0.1 transcendence.test other.test " +
        "# ft_transcendence:managed`n"
    )

    $Result = Remove-ManagedMappingContent `
        -Content $Original -Domain $Domain

    Assert-Equal $Result.Content $Original `
        "Preserve shared aliases"

    # 4. Preserve a conflicting IP.
    $Original = (
        "192.168.1.10 transcendence.test " +
        "# ft_transcendence:managed`n"
    )

    $Result = Remove-ManagedMappingContent `
        -Content $Original -Domain $Domain

    Assert-Equal $Result.Content $Original `
        "Preserve different IP"

    # 5. Cleanup must be idempotent.
    $Original = (
        "127.0.0.1 transcendence.test " +
        "# ft_transcendence:managed`n"
    )

    $First = Remove-ManagedMappingContent `
        -Content $Original -Domain $Domain

    $Second = Remove-ManagedMappingContent `
        -Content $First.Content -Domain $Domain

    Assert-Equal $Second.Changed $false `
        "Cleanup idempotency"

    
    # 6. Test actual file modification on a temporary file.
    $TempDirectory = Join-Path (
        [IO.Path]::GetTempPath()
    ) (
        "ft-transcendence-" +
        [guid]::NewGuid().ToString("N")
    )

    [void][IO.Directory]::CreateDirectory($TempDirectory)

    try {
        $TempHosts = Join-Path $TempDirectory "hosts"

        $Original = (
            "127.0.0.1 localhost`r`n" +
            "127.0.0.1 transcendence.test " +
            "# ft_transcendence:managed`r`n" +
            "192.168.1.10 other-project.test`r`n"
        )

        [IO.File]::WriteAllText(
            $TempHosts,
            $Original,
            [Text.Encoding]::UTF8
        )

        $Result = Remove-ManagedMappingFile `
            -Path $TempHosts `
            -Domain $Domain

        $Expected = (
            "127.0.0.1 localhost`r`n" +
            "192.168.1.10 other-project.test`r`n"
        )

        Assert-Equal $Result.Changed $true `
            "File modification detected"

        Assert-Equal (
            [IO.File]::ReadAllText($TempHosts)
        ) $Expected "File contents updated"

        Assert-Equal (
            [IO.File]::Exists($Result.Backup)
        ) $true "Backup created"

        Assert-Equal (
            [IO.File]::ReadAllText($Result.Backup)
        ) $Original "Backup preserves original"

        # 7. Repeating cleanup must not create a new backup.
        $Second = Remove-ManagedMappingFile `
            -Path $TempHosts `
            -Domain $Domain

        Assert-Equal $Second.Changed $false `
            "File cleanup idempotency"

        Assert-Equal $Second.Backup $null `
            "No backup for unchanged file"
    }
    finally {
        [IO.Directory]::Delete(
            $TempDirectory,
            $true
        )
    }


    Write-Host ""
    Write-Host "All $Passed Windows cleanup tests passed."
    exit 0
}
catch {
    Write-Host "[FAIL] $_"
    exit 1
}
