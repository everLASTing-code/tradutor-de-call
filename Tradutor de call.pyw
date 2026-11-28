"""Tradutor de call: programa de janela propria (sem navegador).

Ouve o que sai no fone/caixa do PC (o mesmo que o LAST escuta no Discord),
transcreve o ingles e mostra em PT. Fica flutuando por cima das outras janelas:
uma barra de pilulas e paineis pretos soltos, sem moldura (a janela e recortada no formato das pecas).
"""
import os
import socket
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ["PYTHONIOENCODING"] = "utf-8"
# pasta do que e da pessoa (config, notas, transcricoes, log). Instalado pelo .exe fica em %LOCALAPPDATA%\TradutorDeCall;
# na pasta do projeto e a propria pasta. (mesma regra do tradutor.py)
FROZEN = bool(getattr(sys, "frozen", False))
DATA = os.environ.get("TRADUTOR_DADOS") or (
    os.path.join(os.environ.get("LOCALAPPDATA") or HERE, "TradutorDeCall") if FROZEN else HERE)
os.makedirs(DATA, exist_ok=True)

# sem console: tudo que o programa imprime vai pra um arquivo de log
log_path = os.path.join(DATA, "tradutor_log_teste.txt" if os.environ.get("TRADUTOR_TESTE") == "1" else "tradutor_log.txt")
_log = open(log_path, "w", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = _log

import webview  # noqa: E402
import janela_win  # noqa: E402

PORT = int(os.environ.get("TRADUTOR_PORTA") or 8770)
URL = f"http://localhost:{PORT}"
# janela de teste (so pra conferir o visual sem mexer na janela que o LAST esta usando):
# TRADUTOR_TESTE=1 abre com outro titulo, nao esconde de print e grava os recortes num arquivo
TESTE = os.environ.get("TRADUTOR_TESTE") == "1"
TITLE = "TLS (teste)" if TESTE else "TLS"   # nome do app (LAST 27/11: "muda o nome")
janela_win.TITLE = TITLE
WIN_W, WIN_H = 700, 900          # janela "invisivel": so aparecem as pecas recortadas
SPLASH_RECT = (200, 14, 300, 52, 26)   # x, y, largura, altura, raio da pilula de carregamento (px CSS)

SPLASH = """<!doctype html><html><body style="margin:0;background:#0b0b0d;color:#f4f4f2;overflow:hidden;
font-family:Segoe UI,system-ui,sans-serif">
<div style="position:fixed;left:200px;top:14px;width:300px;height:52px;display:flex;align-items:center;
justify-content:center;gap:10px;font-size:13.5px;font-weight:600">
<span style="width:8px;height:8px;border-radius:50%;background:#ffd200;flex:none;animation:p .9s ease-in-out infinite alternate"></span>
<span id="t" style="white-space:nowrap">Preparando o tradutor…</span></div>
<style>@keyframes p{from{opacity:.25}to{opacity:1}}</style></body></html>"""


def load_invisivel():
    """Ajustes -> "No print e na tela compartilhada" (config.json, campo invisivel; padrao: invisivel)."""
    try:
        import json
        with open(os.path.join(DATA, "config.json"), encoding="utf8") as f:
            return json.load(f).get("invisivel", True) is not False
    except Exception:
        return True


def save_invisivel(v):
    try:
        import tradutor
        conf = tradutor.load_conf()
        conf["invisivel"] = bool(v)
        tradutor.save_conf(conf)
        tradutor.broadcast({"type": "capture", "v": bool(v)})
    except Exception as e:
        print("nao salvou invisivel:", repr(e), flush=True)


def toggle_capture_key():
    import tradutor
    h = janela_win.toggle_capture(janela_win.hwnd())
    save_invisivel(h)
    tradutor.broadcast({"type": "toast", "text": "Invisível no print" if h else "Agora aparece no print"})


def port_busy():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


class Api:
    """Funcoes que a pagina chama de dentro da janela."""
    def __init__(self):
        self._window = None
        self._top = True

    def toggle_top(self):
        self._top = not self._top
        self._window.on_top = self._top
        return self._top

    def is_app(self):
        return True

    def shape(self, rects):
        """A pagina manda o retangulo de cada peca visivel (em pixels reais) e a janela e recortada nelas."""
        try:
            janela_win.set_region(janela_win.hwnd(), [tuple(r) for r in rects])
            if TESTE:
                import json
                with open(os.path.join(DATA, ".teste_recorte.json"), "w") as f:
                    json.dump(rects, f)
        except Exception as e:
            print("erro no recorte:", repr(e), flush=True)
        return True

    def get_capture(self):
        """True = invisivel em print e tela compartilhada."""
        return janela_win.is_hidden_from_capture(janela_win.hwnd())

    def set_capture(self, hide):
        h = janela_win.set_capture_hidden(janela_win.hwnd(), bool(hide))
        save_invisivel(h)
        return h

    def close(self):
        os._exit(0)

    def set_opacity(self, v):
        self._alpha = max(0.3, min(1.0, float(v)))
        janela_win.set_opacity(janela_win.hwnd(), self._alpha)
        return self._alpha


def setup_hotkeys(api):
    """Atalhos que funcionam mesmo com o Discord na frente."""
    import tradutor
    C, S = janela_win.MOD_CONTROL, janela_win.MOD_SHIFT

    def toggle_pause():
        tradutor.set_paused(not tradutor.state["paused"])

    def copy_sug(n):
        def f():
            if len(tradutor.last_sugs) >= n:
                janela_win.copy_text(tradutor.last_sugs[n - 1]["en"])
                tradutor.broadcast({"type": "copied", "n": n})
        return f

    def opacity(delta):
        def f():
            v = api.set_opacity(getattr(api, "_alpha", 1.0) + delta)
            tradutor.broadcast({"type": "opacity", "v": v})
        return f

    janela_win.hotkeys([
        (C | S, ord("L"), toggle_pause),                      # Ctrl+Shift+L  ligar / desligar a captacao
        (C | S, ord("H"), lambda: janela_win.toggle_visible(janela_win.hwnd())),  # Ctrl+Shift+H esconder
        (C | S, ord("P"), toggle_capture_key),               # Ctrl+Shift+P liga/desliga invisivel no print
        (C | S, ord("R"), lambda: tradutor.broadcast({"type": "suggest_now"})),  # Ctrl+Shift+R pedir respostas
        (C | S, ord("1"), copy_sug(1)),                      # Ctrl+Shift+1/2/3 copiar sugestao
        (C | S, ord("2"), copy_sug(2)),
        (C | S, ord("3"), copy_sug(3)),
        (C | S, 0x26, opacity(+0.1)),                         # Ctrl+Shift+seta cima: mais opaco
        (C | S, 0x28, opacity(-0.1)),                         # Ctrl+Shift+seta baixo: mais transparente
    ])


def splash_region():
    """Enquanto carrega, a janela e so uma pilula pequena (nada de retangulo preto gigante na tela)."""
    import time
    for _ in range(40):
        h = janela_win.hwnd()
        if h:
            k = janela_win.scale(h)
            x, y, w, hh, r = SPLASH_RECT
            janela_win.set_region(h, [(x * k, y * k, w * k, hh * k, r * k)])
            return
        time.sleep(0.05)


def boot(window, api):
    api._window = window
    splash_region()
    if not TESTE and load_invisivel():
        print("invisivel em print/compartilhamento:", janela_win.hide_from_capture(janela_win.hwnd()))
    _ico = os.path.join(getattr(sys, "_MEIPASS", HERE), "icone", "icone.ico")
    if os.path.isfile(_ico):
        janela_win.set_icon(janela_win.hwnd(), _ico)
    try:
        if not port_busy():
            # primeira abertura num PC novo: baixa os arquivos da placa e libera o download dos modelos,
            # mostrando na pilula o que esta fazendo
            import primeira_vez
            import time

            def status(t):
                try:
                    window.evaluate_js("document.getElementById('t').textContent=" + __import__("json").dumps(t))
                except Exception:
                    pass
            # atualizacao automatica (so instalado): se a versao nova ja foi baixada da outra vez, instala agora
            auto_upd = FROZEN and not TESTE and os.environ.get("TRADUTOR_SEM_ATUALIZAR") != "1"
            if auto_upd:
                import atualizar
                try:
                    _v = open(os.path.join(getattr(sys, "_MEIPASS", HERE), "versao.txt"), encoding="utf8").read().strip()
                except Exception:
                    _v = "0"
                pend = atualizar.pendente(DATA, _v)
                if pend:
                    status("Atualizando pra versão nova…")
                    print("instalando atualizacao:", pend)
                    atualizar.instalar(pend)
                    time.sleep(1.0)
                    os._exit(0)
            primeira_vez.preparar(DATA, status)
            baixando = os.environ.get("HF_HUB_OFFLINE") != "1"
            pronto = threading.Event()

            def contador():   # enquanto os modelos baixam, mostra quanto ja veio
                while not pronto.wait(1.5):
                    status(f"Baixando os modelos (só na primeira vez): {primeira_vez.tamanho_hub() / 1e6:,.0f} MB".replace(",", "."))
            if baixando:
                threading.Thread(target=contador, daemon=True).start()
            import tradutor
            try:
                tradutor.start(block=False)
            finally:
                pronto.set()
            setup_hotkeys(api)
            if auto_upd:   # baixa a proxima versao em segundo plano (instala na proxima abertura)
                threading.Thread(target=atualizar.baixar, args=(DATA, _v), daemon=True).start()
        window.load_url(URL)
    except Exception as e:
        import traceback
        traceback.print_exc()
        janela_win.set_region(janela_win.hwnd(), [(0, 0, 4000, 4000, 0)])
        window.load_html(f"<body style='background:#09090a;color:#ff5a52;font-family:sans-serif;padding:24px'>"
                         f"<h3>Não abriu</h3><pre style='white-space:pre-wrap'>{e}</pre>"
                         f"<p style='color:#aaa'>Detalhes em {log_path}</p></body>")


if __name__ == "__main__":
    api = Api()
    win = webview.create_window(
        TITLE, html=SPLASH, js_api=api,
        width=WIN_W, height=WIN_H, y=24, resizable=False,
        x=max(0, (janela_win.user32.GetSystemMetrics(0) - WIN_W) // 2),   # no meio, no alto da tela
        frameless=True, easy_drag=False, shadow=False,
        on_top=True, background_color="#0b0b0d", text_select=True,
    )
    webview.start(boot, (win, api), gui="edgechromium", private_mode=False,
                  storage_path=os.path.join(DATA, ".janela_teste" if TESTE else ".janela"))
    os._exit(0)  # fecha captura e servidor junto com a janela
