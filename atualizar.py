"""Atualizacao automatica do Tradutor de call (so no programa instalado).

Enquanto o programa esta aberto, confere se saiu versao nova no GitHub e baixa o instalador em segundo plano
pra DATA\\atualizacao. Na proxima vez que a pessoa abrir, o programa roda esse instalador sozinho (uns segundos)
e abre ja na versao nova. Nao interrompe call: nunca instala com o programa em uso.
O que e da pessoa (chaves, contexto, termos, notas) fica em DATA e nao e tocado pelo instalador.
"""
import glob
import json
import os
import re
import subprocess
import urllib.request

REPO = "everLASTing-code/tradutor-de-call"
UA = {"User-Agent": "tradutor-de-call"}


def _ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3]) or (0,)


def _pasta(data):
    p = os.path.join(data, "atualizacao")
    os.makedirs(p, exist_ok=True)
    return p


def pendente(data, versao):
    """Instalador ja baixado de uma versao mais nova que a atual (ou None). Apaga os velhos."""
    melhor = None
    for f in glob.glob(os.path.join(_pasta(data), "Instalar-Tradutor-de-Call-*.exe")):
        v = _ver(os.path.basename(f))
        if v > _ver(versao):
            if not melhor or v > _ver(os.path.basename(melhor)):
                melhor = f
        else:
            try:
                os.remove(f)
            except OSError:
                pass
    return melhor


def instalar(caminho):
    """Abre o instalador no modo automatico (ele fecha este programa, troca os arquivos e reabre)."""
    subprocess.Popen([caminho, "--auto"], close_fds=True)


def baixar(data, versao, log=print):
    """Confere a ultima versao e, se for mais nova, baixa o instalador (arquivo .part ate terminar)."""
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                     headers={**UA, "Accept": "application/vnd.github+json"})
        d = json.loads(urllib.request.urlopen(req, timeout=10).read())
        nova = (d.get("tag_name") or "").lstrip("v")
        if _ver(nova) <= _ver(versao):
            return None
        exe = next((a for a in d.get("assets", []) if a.get("name", "").lower().endswith(".exe")), None)
        url = (exe or {}).get("browser_download_url") or ""
        if not url.startswith(f"https://github.com/{REPO}/releases/download/"):
            return None
        dest = os.path.join(_pasta(data), f"Instalar-Tradutor-de-Call-{nova}.exe")
        if os.path.exists(dest) and os.path.getsize(dest) == exe.get("size"):
            return dest
        log("baixando a versao", nova, "pra instalar na proxima abertura")
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r, \
                open(dest + ".part", "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
        if exe.get("size") and os.path.getsize(dest + ".part") != exe["size"]:
            os.remove(dest + ".part")
            log("download da atualizacao veio incompleto, tenta de novo na proxima")
            return None
        os.replace(dest + ".part", dest)
        log("versao", nova, "baixada; instala sozinha na proxima abertura")
        return dest
    except Exception as e:
        log("nao deu pra baixar a atualizacao:", repr(e)[:150])
        return None
