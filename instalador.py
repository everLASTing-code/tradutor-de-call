"""Instalador do Tradutor de call (vira o Instalar-Tradutor-de-Call.exe).

Copia o programa pra %LOCALAPPDATA%\\Programs\\TradutorDeCall (nao pede administrador), cria os atalhos da
Area de Trabalho e do Menu Iniciar e abre o programa. Serve tambem pra atualizar: e so rodar a versao nova
por cima. O que e da pessoa (chaves, notas, transcricoes) fica em outra pasta e nao e tocado.
"""
import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import zipfile

NOME = "Tradutor de call"
RES = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
TESTE = os.environ.get("TRADUTOR_INSTALAR_TESTE")      # pasta de teste: instala la, sem atalho e sem abrir
DEST = TESTE or os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Programs", "TradutorDeCall")
EXE = os.path.join(DEST, "TradutorDeCall.exe")
try:
    VERSAO = open(os.path.join(RES, "versao.txt"), encoding="utf8").read().strip()
except Exception:
    VERSAO = ""


def fechar_aberto():
    """Se o tradutor estiver aberto, fecha (senao o Windows nao deixa trocar os arquivos)."""
    subprocess.run(["taskkill", "/F", "/IM", "TradutorDeCall.exe"], capture_output=True,
                   creationflags=0x08000000)
    time.sleep(1.0)


def atalho(pasta):
    """Cria o atalho .lnk usando o proprio Windows (PowerShell), sem biblioteca extra."""
    lnk = os.path.join(pasta, NOME + ".lnk")
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');$s.TargetPath='%s';"
          "$s.WorkingDirectory='%s';$s.Description='Traduz a call em tempo real';$s.Save()") % (
        lnk.replace("'", "''"), EXE.replace("'", "''"), DEST.replace("'", "''"))
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                   capture_output=True, creationflags=0x08000000)


def pasta_especial(csidl):
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return buf.value


AUTO = "--auto" in sys.argv          # chamado pelo proprio programa pra se atualizar
# pasta do que e da pessoa (mesma regra do tradutor.py); o instalador nunca apaga nada dela
DATA = os.environ.get("TRADUTOR_DADOS") or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                                                        "TradutorDeCall")
# versao "com contexto": o instalador traz a pasta "perfil" (contexto.md, termos.txt, config.json) e
# copia pra pasta da pessoa so o que ela ainda nao tem. As atualizacoes (instalador generico) nao mexem nisso.
PERFIL = os.path.join(RES, "perfil")


def copiar_perfil():
    if not os.path.isdir(PERFIL):
        return
    os.makedirs(DATA, exist_ok=True)
    for n in os.listdir(PERFIL):
        alvo = os.path.join(DATA, n)
        if not os.path.exists(alvo):
            shutil.copyfile(os.path.join(PERFIL, n), alvo)


def instalar(status, progresso):
    if not TESTE:
        status("Fechando a versão antiga…")
        fechar_aberto()
    status("Copiando os arquivos…")
    if os.path.isdir(DEST):
        shutil.rmtree(DEST, ignore_errors=True)
    os.makedirs(DEST, exist_ok=True)
    with zipfile.ZipFile(os.path.join(RES, "app.zip")) as z:
        nomes = z.namelist()
        for i, n in enumerate(nomes, start=1):
            z.extract(n, DEST)
            if i % 25 == 0 or i == len(nomes):
                progresso(i / len(nomes))
    copiar_perfil()
    if TESTE:
        status("Pronto (teste).")
        return
    status("Criando os atalhos…")
    atalho(pasta_especial(0x10))                                   # Area de Trabalho
    atalho(os.path.join(pasta_especial(0x02)))                     # Menu Iniciar > Programas
    status("Pronto. Abrindo o tradutor…")
    subprocess.Popen([EXE], cwd=DEST, close_fds=True)


def main():
    root = tk.Tk()
    root.title("Instalar " + NOME)
    root.configure(bg="#0b0b0d")
    root.resizable(False, False)
    w, h = 440, 190
    root.geometry(f"{w}x{h}+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 2}")
    tk.Label(root, text=NOME + (f"  {VERSAO}" if VERSAO else ""), bg="#0b0b0d", fg="#f5f5f4",
             font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=24, pady=(22, 2))
    msg = tk.Label(root, text="Atualizando pra versão nova…" if AUTO else "Preparando…", bg="#0b0b0d", fg="#9a9a9e", font=("Segoe UI", 10), anchor="w",
                   justify="left", wraplength=392)
    msg.pack(fill="x", padx=24)
    barra = tk.Canvas(root, height=6, bg="#202023", highlightthickness=0)
    barra.pack(fill="x", padx=24, pady=(18, 0))
    fill = barra.create_rectangle(0, 0, 0, 6, fill="#ffd200", width=0)
    nota = tk.Label(root, text="Na primeira abertura o tradutor baixa os modelos (uns 2 GB). Precisa de internet.",
                    bg="#0b0b0d", fg="#63636a", font=("Segoe UI", 9), anchor="w", justify="left", wraplength=392)
    nota.pack(fill="x", padx=24, pady=(14, 0))

    def status(t):
        root.after(0, lambda: msg.config(text=t))

    def progresso(v):
        root.after(0, lambda: barra.coords(fill, 0, 0, barra.winfo_width() * v, 6))

    def run():
        try:
            instalar(status, progresso)
            root.after(1200, root.destroy)
        except Exception as e:
            status("Não deu pra instalar: " + str(e)[:160])

    threading.Thread(target=run, daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()
