
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

    

    Write-Host ""
    Write-Host "All $Passed Windows cleanup tests passed."
    exit 0
}
catch {
    Write-Host "[FAIL] $_"
    exit 1
}
