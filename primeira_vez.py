"""Primeira abertura num PC novo: baixa o que nao cabe no instalador.

- Modelos (Whisper + tradutores): o proprio faster-whisper/ctranslate2 baixa do Hugging Face quando faltam.
  Aqui so se decide se o app pode abrir "offline" (mais rapido) e se mostra o tamanho ja baixado.
- Arquivos da placa NVIDIA (cuBLAS + cuDNN, ~1,3 GB): so em PC com placa NVIDIA. Vem dos pacotes oficiais
  no PyPI, conferidos pelo sha256 que o PyPI informa, e ficam em DATA\\nvidia.
"""
import glob
import hashlib
import json
import os
import site
import sys
import tempfile
import urllib.request
import zipfile

HUB = os.path.join(os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface"), "hub")
MODELOS_GPU = ["mobiuslabsgmbh/faster-whisper-large-v3-turbo"]
MODELOS_CPU = ["Systran/faster-whisper-small"]
MODELOS_TRAD = ["ooeoeo/opus-mt-tc-big-en-pt-ct2-float16", "ooeoeo/opus-mt-ROMANCE-en-ct2-float16"]
PACOTES_GPU = [("nvidia-cublas-cu12", "12.9.2.10"), ("nvidia-cudnn-cu12", "9.28.1.4")]   # as mesmas versoes testadas
UA = {"User-Agent": "tradutor-de-call"}


def tem_nvidia():
    """Driver da NVIDIA instalado = tem placa que serve."""
    return os.path.exists(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvcuda.dll"))


def _dll_dirs(data):
    dirs = glob.glob(os.path.join(data, "nvidia", "*", "bin"))
    if not getattr(sys, "frozen", False):          # na pasta do projeto, as DLLs vem do pip
        try:
            for sp in site.getsitepackages():
                dirs += glob.glob(os.path.join(sp, "nvidia", "*", "bin"))
        except Exception:
            pass
    return dirs


def gpu_pronta(data):
    nomes = {os.path.basename(f).lower() for d in _dll_dirs(data) for f in glob.glob(os.path.join(d, "*.dll"))}
    return "cublas64_12.dll" in nomes and "cudnn64_9.dll" in nomes


def modelos_prontos(gpu):
    need = (MODELOS_GPU if gpu else MODELOS_CPU) + MODELOS_TRAD
    return all(glob.glob(os.path.join(HUB, "models--" + n.replace("/", "--"), "snapshots", "*", "model.bin")) for n in need)


def tamanho_hub():
    total = 0
    for root, _, files in os.walk(os.path.join(HUB)):
        if os.sep + "blobs" in root:
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    return total


def baixar_gpu(data, status=lambda t: None):
    """Baixa cuBLAS e cuDNN do PyPI e guarda so as DLLs em DATA\\nvidia\\<lib>\\bin."""
    for i, (pkg, ver) in enumerate(PACOTES_GPU, start=1):
        meta = json.loads(urllib.request.urlopen(
            urllib.request.Request(f"https://pypi.org/pypi/{pkg}/{ver}/json", headers=UA), timeout=20).read())
        arq = next(u for u in meta["urls"] if u["filename"].endswith("win_amd64.whl"))
        tmp = os.path.join(tempfile.gettempdir(), arq["filename"])
        h, done, total, last = hashlib.sha256(), 0, int(arq.get("size") or 0), -1
        with urllib.request.urlopen(urllib.request.Request(arq["url"], headers=UA), timeout=60) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                pct = int(done * 100 / total) if total else 0
                if pct != last:
                    last = pct
                    status(f"Baixando arquivos da placa de vídeo {i}/{len(PACOTES_GPU)}: {pct}%")
        if h.hexdigest() != arq["digests"]["sha256"]:
            os.remove(tmp)
            raise RuntimeError(f"download corrompido: {pkg}")
        status(f"Instalando arquivos da placa de vídeo {i}/{len(PACOTES_GPU)}…")
        with zipfile.ZipFile(tmp) as z:
            for m in z.namelist():
                p = m.replace("\\", "/")
                if p.startswith("nvidia/") and "/bin/" in p and p.lower().endswith(".dll") and ".." not in p:
                    dest = os.path.join(data, *p.split("/"))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with z.open(m) as src, open(dest, "wb") as out:
                        while True:
                            b = src.read(1 << 20)
                            if not b:
                                break
                            out.write(b)
        os.remove(tmp)


def preparar(data, status=lambda t: None):
    """Chamado antes de carregar os modelos. Devolve True se vai usar a placa de video."""
    gpu = tem_nvidia()
    if gpu and not gpu_pronta(data):
        try:
            baixar_gpu(data, status)
        except Exception as e:
            print("nao baixou os arquivos da placa:", repr(e)[:200], flush=True)
        gpu = gpu_pronta(data)
    if not gpu:
        os.environ["TRADUTOR_SEM_GPU"] = "1"          # nem tenta a placa (e nao baixa o modelo grande a toa)
    if modelos_prontos(gpu):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")   # tudo ja esta no PC: abre sem ir na internet
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)
        status("Baixando os modelos (só na primeira vez)…")
    return gpu
