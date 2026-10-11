
function Remove-ManagedMappingContent {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Content,

        [Parameter(Mandatory = $true)]
        [string]$Domain,

        [string]$ExpectedIP = "127.0.0.1"
    )

    $Marker = "ft_transcendence:managed"
    $Builder = New-Object System.Text.StringBuilder
    $Removed = 0

    # Split into lines while preserving original line endings.
    $Pattern = '[^\r\n]*(?:\r\n|\r|\n|$)'

    foreach ($Match in [regex]::Matches($Content, $Pattern)) {
        $Line = $Match.Value

        if ($Line.Length -eq 0) {
            continue
        }

        $Body = [regex]::Replace(
            $Line,
            '(?:\r\n|\r|\n)$',
            ''
        )

        $Parts = $Body -split '#', 2
        $ShouldRemove = $false

        if (
            $Parts.Count -eq 2 -and
            $Parts[1].Trim() -ceq $Marker
        ) {
            $Fields = $Parts[0].Trim() -split '\s+'

            if (
                $Fields.Count -eq 2 -and
                $Fields[0] -eq $ExpectedIP -and
                $Fields[1] -ieq $Domain
            ) {
                $ShouldRemove = $true
            }
        }

        if ($ShouldRemove) {
            $Removed++
        }
        else {
            [void]$Builder.Append($Line)
        }
    }

    return [pscustomobject]@{
        Content = $Builder.ToString()
        Removed = $Removed
        Changed = ($Removed -gt 0)
    }
}


function Remove-ManagedMappingFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Domain
    )

    $OriginalBytes = [IO.File]::ReadAllBytes($Path)

    # Do not modify UTF-16 hosts files with this byte-oriented editor.
    if ($OriginalBytes.Length -ge 2) {
        if (
            ($OriginalBytes[0] -eq 0xFF -and
             $OriginalBytes[1] -eq 0xFE) -or
            ($OriginalBytes[0] -eq 0xFE -and
             $OriginalBytes[1] -eq 0xFF)
        ) {
            throw "UTF-16 hosts files are not supported"
        }
    }

    # Latin-1 maps every byte to one character.
    # Unmodified bytes remain unchanged, including UTF-8 text.
    $Encoding = [Text.Encoding]::GetEncoding(28591)
    $Content = $Encoding.GetString($OriginalBytes)

    $HasBom = (
        $OriginalBytes.Length -ge 3 -and
        $OriginalBytes[0] -eq 0xEF -and
        $OriginalBytes[1] -eq 0xBB -and
        $OriginalBytes[2] -eq 0xBF
    )

    if ($HasBom) {
        $Prefix = $Content.Substring(0, 3)
        $Content = $Content.Substring(3)
    }
    else {
        $Prefix = ""
    }

    $Result = Remove-ManagedMappingContent `
        -Content $Content `
        -Domain $Domain

    if (-not $Result.Changed) {
        return [pscustomobject]@{
            Changed = $false
            Removed = 0
            Backup = $null
        }
    }

    $Directory = [IO.Path]::GetDirectoryName(
        [IO.Path]::GetFullPath($Path)
    )

    $Id = [guid]::NewGuid().ToString("N")

    $Temporary = Join-Path $Directory (
        ".ft-transcendence-$Id.tmp"
    )

    $Backup = Join-Path $Directory (
        ".ft-transcendence-$Id.bak"
    )

    try {
        $UpdatedBytes = $Encoding.GetBytes(
            $Prefix + $Result.Content
        )

        [IO.File]::WriteAllBytes(
            $Temporary,
            $UpdatedBytes
        )

        # Detect changes since the original read.
        $CurrentBytes = [IO.File]::ReadAllBytes($Path)

        if (
            [Convert]::ToBase64String($CurrentBytes) -cne
            [Convert]::ToBase64String($OriginalBytes)
        ) {
            throw "Hosts file changed during cleanup"
        }

        # Replace destination and preserve its previous
        # contents in the backup file.
        [IO.File]::Replace(
            $Temporary,
            $Path,
            $Backup,
            $true
        )

        return [pscustomobject]@{
            Changed = $true
            Removed = $Result.Removed
            Backup = $Backup
        }
    }
    finally {
        if ([IO.File]::Exists($Temporary)) {
            [IO.File]::Delete($Temporary)
        }
    }
}
