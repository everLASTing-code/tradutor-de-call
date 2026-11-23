# Gera o programa (pasta dist\TradutorDeCall) e o instalador (dist\Instalar-Tradutor-de-Call.exe).
# Uso:  powershell -ExecutionPolicy Bypass -File gerar_exe.ps1
# Precisa: Python com as dependencias do requirements.txt e o PyInstaller (pip install pyinstaller).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$py = if ($env:TRADUTOR_PYTHON) { $env:TRADUTOR_PYTHON } else { "python" }
$ver = (Get-Content "$here\versao.txt" -Raw).Trim()
Write-Host "Gerando a versao $ver"

# 1) o programa em pasta (abre rapido; nao descompacta nada a cada abertura)
& $py -m PyInstaller --noconfirm --clean --windowed --name TradutorDeCall `
  --distpath "$here\dist" --workpath "$here\build" --specpath "$here\build" `
  --add-data "$here\pagina.html;." --add-data "$here\gsap.min.js;." --add-data "$here\versao.txt;." `
  --add-data "$here\contexto_padrao.md;." --add-data "$here\termos_padrao.txt;." `
  --hidden-import tradutor --hidden-import janela_win --hidden-import primeira_vez `
  --collect-data faster_whisper --collect-all ctranslate2 --collect-all onnxruntime `
  --collect-all av --collect-all clr_loader --collect-all pythonnet --collect-all webview `
  --collect-all soundcard --collect-submodules tokenizers --collect-submodules huggingface_hub `
  --exclude-module torch --exclude-module matplotlib --exclude-module tkinter --exclude-module PIL `
  --exclude-module nvidia --exclude-module soundfile --exclude-module IPython --exclude-module pytest `
  "$here\Tradutor de call.pyw"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou" }

# 2) compacta a pasta do programa pra ir dentro do instalador
$zip = "$here\build\app.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "$here\dist\TradutorDeCall\*" -DestinationPath $zip -CompressionLevel Optimal

# 3) o instalador: um .exe so, que copia o programa pro PC da pessoa e cria os atalhos
& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name "Instalar-Tradutor-de-Call" `
  --distpath "$here\dist" --workpath "$here\build\inst" --specpath "$here\build" `
  --add-data "$zip;." --add-data "$here\versao.txt;." `
  --exclude-module numpy --exclude-module PIL --exclude-module matplotlib `
  "$here\instalador.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller (instalador) falhou" }
Get-Item "$here\dist\Instalar-Tradutor-de-Call.exe" | ForEach-Object { "{0}  {1:N0} MB" -f $_.FullName, ($_.Length / 1MB) }
