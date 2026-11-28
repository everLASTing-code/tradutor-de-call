"""Coisas do Windows pro programa: atalhos globais, transparencia, esconder janela e area de transferencia."""
import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x2
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x2, 0x4, 0x4000
WM_HOTKEY = 0x0312

user32.FindWindowW.restype = wintypes.HWND
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]


TITLE = "TLS"     # o .pyw troca isso quando abre uma janela de teste com outro titulo


def hwnd(title=None):
    return user32.FindWindowW(None, title or TITLE)


def set_opacity(h, alpha):
    """alpha 0.3 a 1.0"""
    if not h:
        return
    style = user32.GetWindowLongPtrW(h, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(h, GWL_EXSTYLE, style | WS_EX_LAYERED)
    user32.SetLayeredWindowAttributes(h, 0, int(max(0.3, min(1.0, alpha)) * 255), LWA_ALPHA)


gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
gdi32.CreateRectRgn.restype = wintypes.HRGN
gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
gdi32.CombineRgn.argtypes = [wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, ctypes.c_int]
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]


def scale(h):
    """Escala do monitor (1.0 = 100%, 1.25 = 125%...)."""
    try:
        return (user32.GetDpiForWindow(h) or 96) / 96.0
    except Exception:
        return 1.0


def set_region(h, rects):
    """Recorta a janela no formato das pecas (pilulas e paineis): so elas existem na tela, o resto
    nao aparece nem pega clique. rects = [(x, y, largura, altura, raio)] em pixels da janela."""
    if not h:
        return
    rgn = gdi32.CreateRectRgn(0, 0, 0, 0)
    for x, y, w, hh, r in rects:
        x, y, w, hh, r = int(round(x)), int(round(y)), int(round(w)), int(round(hh)), int(round(r))
        if w <= 0 or hh <= 0:
            continue
        piece = gdi32.CreateRoundRectRgn(x, y, x + w + 1, y + hh + 1, 2 * r, 2 * r)
        gdi32.CombineRgn(rgn, rgn, piece, 2)    # RGN_OR
        gdi32.DeleteObject(piece)
    user32.SetWindowRgn(h, rgn, True)           # o Windows passa a ser dono da regiao
    no_backdrop(h)


_dwm = ctypes.WinDLL("dwmapi")


def no_backdrop(h):
    """Bloco cinza (LAST 22/11): o Windows 11 pinta um fundo de sistema (#202020) na janela inteira, por baixo do
    recorte, e ele aparecia em volta das pilulas/paineis. DWMWA_SYSTEMBACKDROP_TYPE (38) = 1 (nenhum) tira esse fundo.
    Testado numa janela pywebview igual: com isso, fora das pecas aparece o que esta atras."""
    if not h:
        return
    v = ctypes.c_int(1)
    try:
        _dwm.DwmSetWindowAttribute(h, 38, ctypes.byref(v), 4)
    except Exception:
        pass


WDA_EXCLUDEFROMCAPTURE = 0x11
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]


def set_icon(h, path):
    """Poe o icone do app na janela e na barra de tarefas (no modo pasta o processo e o pythonw)."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TLS.Tradutor")
        load = user32.LoadImageW
        load.restype = wintypes.HANDLE
        for which, size in ((1, 0), (0, 16)):   # ICON_BIG (tamanho padrao) e ICON_SMALL
            ic = load(None, path, 1, size, size, 0x10 | (0x40 if not size else 0))
            if ic:
                user32.SendMessageW(h, 0x80, which, ic)
        return True
    except Exception as e:
        print("icone:", e)
        return False


def hide_from_capture(h):
    """A janela some de print, gravacao e compartilhamento de tela (Discord, OBS, Meet...).
    Na tua tela ela continua aparecendo normal. Precisa de Windows 10 2004 ou mais novo."""
    if not h:
        return False
    return bool(user32.SetWindowDisplayAffinity(h, WDA_EXCLUDEFROMCAPTURE))


user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


def is_hidden_from_capture(h):
    cur = wintypes.DWORD(0)
    if h:
        user32.GetWindowDisplayAffinity(h, ctypes.byref(cur))
    return cur.value != 0


def set_capture_hidden(h, hide):
    if not h:
        return False
    user32.SetWindowDisplayAffinity(h, WDA_EXCLUDEFROMCAPTURE if hide else 0)
    return is_hidden_from_capture(h)


def toggle_capture(h):
    """Ctrl+Shift+P: liga/desliga o invisivel (pra conseguir tirar print do app quando quiser).
    Devolve True se ficou invisivel."""
    if not h:
        return None
    cur = wintypes.DWORD(0)
    user32.GetWindowDisplayAffinity(h, ctypes.byref(cur))
    hide = cur.value == 0
    user32.SetWindowDisplayAffinity(h, WDA_EXCLUDEFROMCAPTURE if hide else 0)
    return hide


def toggle_visible(h):
    if not h:
        return
    if user32.IsWindowVisible(h):
        user32.ShowWindow(h, 0)          # SW_HIDE
    else:
        user32.ShowWindow(h, 5)          # SW_SHOW
        user32.SetForegroundWindow(h)


def copy_text(text):
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        threading.Event().wait(0.05)
    else:
        return False
    try:
        user32.EmptyClipboard()
        hmem = kernel32.GlobalAlloc(0x0042, size)   # GMEM_MOVEABLE | GMEM_ZEROINIT
        p = kernel32.GlobalLock(hmem)
        ctypes.memmove(p, data, size)
        kernel32.GlobalUnlock(hmem)
        user32.SetClipboardData(13, hmem)           # CF_UNICODETEXT
        return True
    finally:
        user32.CloseClipboard()


def hotkeys(bindings):
    """bindings: lista de (mods, vk, funcao). Roda numa thread com fila de mensagens propria."""
    def run():
        ok = []
        for i, (mods, vk, _) in enumerate(bindings, start=1):
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                ok.append(i)
            else:
                print("atalho ocupado por outro programa:", i, flush=True)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY and 1 <= msg.wParam <= len(bindings):
                try:
                    bindings[msg.wParam - 1][2]()
                except Exception as e:
                    print("erro no atalho:", repr(e), flush=True)
    threading.Thread(target=run, daemon=True).start()
