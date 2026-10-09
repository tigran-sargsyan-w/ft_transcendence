
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

