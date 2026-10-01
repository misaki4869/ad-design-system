$ErrorActionPreference = 'Stop'

$projectDir = [System.IO.Path]::GetDirectoryName($MyInvocation.MyCommand.Path)
$venvDir = Join-Path -Path $projectDir -ChildPath '.venv'
$venvPython = Join-Path -Path $venvDir -ChildPath 'Scripts\python.exe'
$requirementsPath = Join-Path -Path $projectDir -ChildPath 'requirements.txt'
$appPath = Join-Path -Path $projectDir -ChildPath 'app.py'
$codexPython = Join-Path -Path $env:USERPROFILE -ChildPath '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'

Set-Location -LiteralPath $projectDir

if (-not (Test-Path -LiteralPath $venvPython)) {
    $pythonCommand = Get-Command -Name 'python.exe' -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        $pythonCommand = Get-Command -Name 'py.exe' -ErrorAction SilentlyContinue
    }

    if ($pythonCommand) {
        $basePython = $pythonCommand.Source
    }
    elseif (Test-Path -LiteralPath $codexPython) {
        $basePython = $codexPython
    }
    else {
        Write-Host 'Python 3.11 or later was not found.' -ForegroundColor Red
        Write-Host 'Install Python, then run start_app.bat again.' -ForegroundColor Yellow
        exit 1
    }

    & $basePython -m venv $venvDir
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & $venvPython -m pip install --disable-pip-version-check -r $requirementsPath
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

& $venvPython -m streamlit run $appPath
exit $LASTEXITCODE
