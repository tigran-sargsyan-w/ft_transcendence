
param(
    [ValidateSet("check", "plan")]
    [string]$Action = "check",

    [Parameter(Mandatory = $true)]
    [string]$CaPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9A-Fa-f]{40}$")]
    [string]$ExpectedThumbprint
)

$ErrorActionPreference = "Stop"

try {
    if (-not (Test-Path -LiteralPath $CaPath -PathType Leaf)) {
        throw "CA certificate file not found: $CaPath"
    }

    # Read the public CA certificate.
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

    # Confirm the certificate is a CA.
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
                $Expected,
                $false
            )

            if ($Matches.Count -gt 0) {
                Write-Output "[OK] $Location Root: CA trusted"
                $Trusted = $true
            }
            else {
                Write-Output "[MISSING] $Location Root"
            }
        }
        finally {
            $Store.Close()
        }
    }

    if ($Trusted) {
        Write-Output "[OK] Windows CA trust configured"
        exit 0
    }

    if ($Action -eq "plan") {
        Write-Output "[PLAN] Import public CA certificate into CurrentUser Root"
        Write-Output "[INFO] Preview only. No system changes made."
        exit 0
    }

    Write-Output "[TRUST] Windows CA trust setup required"
    exit 1
}
catch {
    [Console]::Error.WriteLine("[ERROR] $($_.Exception.Message)")
    exit 2
}
