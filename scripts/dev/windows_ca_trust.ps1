
param(
    [ValidateSet("check", "plan", "setup", "uninstall")]
    [string]$Action = "check",

    [Parameter(Mandatory = $true)]
    [string]$CaPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9A-Fa-f]{40}$")]
    [string]$ExpectedThumbprint,

    [switch]$Apply
)

$ErrorActionPreference = "Stop"

function Test-CaTrust {
    param(
        [string]$Thumbprint
    )

    $Trusted = $false

    foreach ($Location in @(
        [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser,
        [System.Security.Cryptography.X509Certificates.StoreLocation]::LocalMachine
    )) {
        $Store = [System.Security.Cryptography.X509Certificates.X509Store]::new(
            [System.Security.Cryptography.X509Certificates.StoreName]::Root,
            $Location
        )

        try {
            $Store.Open(
                [System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly
            )

            $Matches = $Store.Certificates.Find(
                [System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,
                $Thumbprint,
                $false
            )

            if ($Matches.Count -gt 0) {
                Write-Host "[OK] $Location Root: CA trusted"
                $Trusted = $true
            }
            else {
                Write-Host "[MISSING] $Location Root"
            }
        }
        finally {
            $Store.Close()
        }
    }

    return $Trusted
}


function Install-CurrentUserCa {
    param(
        [System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate
    )

    $Store = [System.Security.Cryptography.X509Certificates.X509Store]::new(
        [System.Security.Cryptography.X509Certificates.StoreName]::Root,
        [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
    )

    try {
        $Store.Open(
            [System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite
        )

        $Store.Add($Certificate)
    }
    finally {
        $Store.Close()
    }
}


try {
    if (-not (Test-Path -LiteralPath $CaPath -PathType Leaf)) {
        throw "CA certificate file not found: $CaPath"
    }

    # Load the public CA certificate.
    $Certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
        $CaPath
    )

    $Expected = $ExpectedThumbprint.ToUpperInvariant()
    $Actual = $Certificate.Thumbprint.ToUpperInvariant()

    if ($Actual -ne $Expected) {
        throw "CA thumbprint mismatch"
    }

    if ($Certificate.HasPrivateKey) {
        throw "CA input must not contain a private key"
    }

    # Confirm this certificate is a CA.
    $BasicConstraints = $Certificate.Extensions |
        Where-Object {
            $_ -is [System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]
        } |
        Select-Object -First 1

    if (
        $null -eq $BasicConstraints -or
        -not $BasicConstraints.CertificateAuthority
    ) {
        throw "Certificate is not a CA"
    }

    if ($Certificate.Subject -ne $Certificate.Issuer) {
        throw "Expected a self-issued root CA certificate"
    }

    Write-Output "[CA] $($Certificate.Subject)"
    Write-Output "[THUMBPRINT] $Actual"


    if ($Action -eq "uninstall") {
        $Store = [System.Security.Cryptography.X509Certificates.X509Store]::new(
            [System.Security.Cryptography.X509Certificates.StoreName]::Root,
            [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
        )

        # Check only CurrentUser Root.
        try {
            $Store.Open(
                [System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly
            )

            $Matches = $Store.Certificates.Find(
                [System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,
                $Expected,
                $false
            )

            $Present = $Matches.Count -gt 0
        }
        finally {
            $Store.Close()
        }

        if (-not $Present) {
            Write-Output "[OK] CA is not in CurrentUser Root"
            Write-Output "[PRESERVED] LocalMachine Root"
            exit 0
        }

        Write-Output "[PLAN] Remove matching CA from CurrentUser Root"
        Write-Output "[PRESERVED] LocalMachine Root"
        Write-Output "[PRESERVED] CA files and project TLS certificates"

        if (-not $Apply) {
            Write-Output "[INFO] Preview only. Use -Action uninstall -Apply"
            exit 1
        }

        # Remove only certificates matching the expected thumbprint.
        try {
            $Store.Open(
                [System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite
            )

            $Matches = $Store.Certificates.Find(
                [System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,
                $Expected,
                $false
            )

            foreach ($Match in $Matches) {
                $Store.Remove($Match)
            }
        }
        finally {
            $Store.Close()
        }

        # Verify that CurrentUser Root no longer contains this CA.
        try {
            $Store.Open(
                [System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly
            )

            $Remaining = $Store.Certificates.Find(
                [System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,
                $Expected,
                $false
            )

            if ($Remaining.Count -gt 0) {
                throw "CA still exists in CurrentUser Root"
            }
        }
        finally {
            $Store.Close()
        }

        Write-Output "[OK] CA removed from CurrentUser Root"
        Write-Output "[OK] CA files and LocalMachine Root preserved"
        exit 0
    }


    # Check both user and machine trust stores.
    $Trusted = Test-CaTrust -Thumbprint $Expected

    if ($Trusted) {
        Write-Output "[OK] Windows CA trust already configured"
        exit 0
    }

    # Preview mode.
    if ($Action -eq "plan") {
        Write-Output "[PLAN] Import public CA into CurrentUser Root"
        Write-Output "[INFO] Preview only. No changes made."
        exit 0
    }

    # Diagnostic mode.
    if ($Action -eq "check") {
        Write-Output "[TRUST] Windows CA trust setup required"
        exit 1
    }

    # Setup requires an explicit -Apply flag.
    if (-not $Apply) {
        Write-Output "[PLAN] Import public CA into CurrentUser Root"
        Write-Output "[INFO] Use -Action setup -Apply to confirm"
        exit 1
    }

    Write-Output "[TRUST] Installing CA into CurrentUser Root..."

    Install-CurrentUserCa -Certificate $Certificate

    # Verify installation.
    $Trusted = Test-CaTrust -Thumbprint $Expected

    if (-not $Trusted) {
        throw "CA was imported but trust verification failed"
    }

    Write-Output "[OK] Windows CA trust configured successfully"
    exit 0
}
catch {
    [Console]::Error.WriteLine(
        "[ERROR] $($_.Exception.Message)"
    )
    exit 2
}
