param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $Python -m pip install -r requirements.txt 'pyinstaller==6.22.2'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    & $Python -m PyInstaller --noconfirm --onedir --windowed --name CS1ToTpF2 --distpath ../app --workpath build --specpath build --paths . main.py
    if ($LASTEXITCODE -ne 0) { throw 'Application build failed' }
} finally {
    Pop-Location
}
