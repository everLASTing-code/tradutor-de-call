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
& $py -m PyInstaller --noconfirm --clean --windowed --name TradutorDeCall --icon "$here\icone\icone.ico" --add-data "$here\icone;icone" `
  --distpath "$here\dist" --workpath "$here\build" --specpath "$here\build" `
  --add-data "$here\pagina.html;." --add-data "$here\gsap.min.js;." --add-data "$here\fonts;fonts" --add-data "$here\versao.txt;." `
  --add-data "$here\contexto_padrao.md;." --add-data "$here\termos_padrao.txt;." `
  --hidden-import tradutor --hidden-import janela_win --hidden-import primeira_vez --hidden-import atualizar `
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
& $py -m PyInstaller --noconfirm --clean --onefile --windowed --icon "$here\icone\icone.ico" --name "Instalar-Tradutor-de-Call" `
  --distpath "$here\dist" --workpath "$here\build\inst" --specpath "$here\build" `
  --add-data "$zip;." --add-data "$here\versao.txt;." `
  --exclude-module numpy --exclude-module PIL --exclude-module matplotlib `
  "$here\instalador.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller (instalador) falhou" }
Get-Item "$here\dist\Instalar-Tradutor-de-Call.exe" | ForEach-Object { "{0}  {1:N0} MB" -f $_.FullName, ($_.Length / 1MB) }

# 4) versao COM CONTEXTO (so no PC de quem tem os arquivos locais, que nunca vao pro GitHub): o mesmo programa +
#    a pasta "perfil" com contexto, termos e config. O instalador copia esses arquivos pra pasta da pessoa so se ela
#    ainda nao tiver, e as atualizacoes automaticas (instalador generico do GitHub) nao mexem neles.
#    Nome do instalador: Instalar-Tradutor-de-Call-<TRADUTOR_PERFIL>.exe (padrao "Contexto").
if (Test-Path "$here\contexto_crown.md") {
  $nomePerfil = if ($env:TRADUTOR_PERFIL) { $env:TRADUTOR_PERFIL } else { "Contexto" }
  $perfil = "$here\build\perfil"
  if (Test-Path $perfil) { Remove-Item $perfil -Recurse -Force }
  New-Item -ItemType Directory -Force $perfil | Out-Null
  Copy-Item "$here\contexto_crown.md" "$perfil\contexto.md"
  if (Test-Path "$here\termos.txt") { Copy-Item "$here\termos.txt" "$perfil\termos.txt" }
  if (Test-Path "$here\perfil_config.json") { Copy-Item "$here\perfil_config.json" "$perfil\config.json" }
  & $py -m PyInstaller --noconfirm --clean --onefile --windowed --icon "$here\icone\icone.ico" --name "Instalar-Tradutor-de-Call-$nomePerfil" `
    --distpath "$here\dist" --workpath "$here\build\inst-perfil" --specpath "$here\build" `
    --add-data "$zip;." --add-data "$here\versao.txt;." --add-data "$perfil;perfil" `
    --exclude-module numpy --exclude-module PIL --exclude-module matplotlib `
    "$here\instalador.py"
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller (instalador com contexto) falhou" }
  Get-Item "$here\dist\Instalar-Tradutor-de-Call-$nomePerfil.exe" | ForEach-Object { "{0}  {1:N0} MB" -f $_.FullName, ($_.Length / 1MB) }
}

# 5) nomes com a marca (Ponte, 27/11). O instalador do GitHub continua "Instalar-Tradutor-de-Call.exe"
#    porque a atualizacao automatica das versoes antigas procura esse arquivo; aqui saem copias com o nome novo.
Get-ChildItem "$here\dist\Instalar-Tradutor-de-Call*.exe" | ForEach-Object {
  Copy-Item $_.FullName (Join-Path $_.DirectoryName ($_.Name -replace "^Instalar-Tradutor-de-Call", "Instalar-Ponte")) -Force }
