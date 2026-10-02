$ErrorActionPreference = "Stop"

$framework = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319"
$compiler = Join-Path $framework "csc.exe"
$outputDirectory = Join-Path $PSScriptRoot "bin"
$output = Join-Path $outputDirectory "AutomateJev.WindowsHost.exe"
$smokeOutput = Join-Path $outputDirectory "AutomateJev.SmokeTarget.exe"

$references = @(
    (Join-Path $framework "System.Web.Extensions.dll"),
    (Join-Path $framework "WPF\UIAutomationClient.dll"),
    (Join-Path $framework "WPF\UIAutomationTypes.dll"),
    (Join-Path $framework "WPF\WindowsBase.dll")
)

if (!(Test-Path -LiteralPath $compiler)) {
    throw "64-bit .NET Framework C# compiler is unavailable."
}
foreach ($reference in $references) {
    if (!(Test-Path -LiteralPath $reference)) {
        throw "Required framework assembly is unavailable: $reference"
    }
}

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

$arguments = @(
    "/nologo",
    "/target:exe",
    "/platform:x64",
    "/optimize+",
    "/warnaserror+",
    "/out:$output"
)
foreach ($reference in $references) {
    $arguments += "/reference:$reference"
}
$arguments += (Join-Path $PSScriptRoot "AutomateJev.WindowsHost.cs")

& $compiler @arguments
if ($LASTEXITCODE -ne 0) {
    throw "C# compilation failed with exit code $LASTEXITCODE"
}

$smokeArguments = @(
    "/nologo",
    "/target:winexe",
    "/platform:x64",
    "/optimize+",
    "/warnaserror+",
    "/out:$smokeOutput",
    "/reference:System.Windows.Forms.dll",
    "/reference:System.Drawing.dll",
    (Join-Path $PSScriptRoot "AutomateJev.SmokeTarget.cs")
)
& $compiler @smokeArguments
if ($LASTEXITCODE -ne 0) {
    throw "Smoke target compilation failed with exit code $LASTEXITCODE"
}

Write-Output $output
Write-Output $smokeOutput
