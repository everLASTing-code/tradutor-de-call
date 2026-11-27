"""Tradutor de call do LAST.

Ouve o audio do PC (o que sai no fone/caixa, ex.: Discord), transcreve o ingles
com faster-whisper e mostra a traducao em PT-BR numa pagina local.
Tambem traduz o que o LAST escreve em PT para um ingles curto e informal.

Tudo roda offline no PC: faster-whisper (small.en) + modelos opus-mt em ctranslate2.
Abrir: http://localhost:8770
"""
import json
import os
import queue
import re
import sys
import threading
import time
import warnings
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import glob
import site

# bibliotecas CUDA (pip nvidia-cublas-cu12 / nvidia-cudnn-cu12) pra usar a GTX 1060
try:
    _sps = site.getsitepackages()
except Exception:      # dentro do .exe nao existe site-packages
    _sps = []
for _sp in _sps:
    for _d in glob.glob(os.path.join(_sp, "nvidia", "*", "bin")):
        try:
            os.add_dll_directory(_d)
            os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
        except OSError:
            pass

import numpy as np

warnings.filterwarnings("ignore")

PORT = int(os.environ.get("TRADUTOR_PORTA") or 8770)
SR = 16000
BLOCK = 1600  # 0,1 s
HERE = os.path.dirname(os.path.abspath(__file__))
# Pastas (pra funcionar tanto na pasta do projeto quanto instalado em outro PC):
#   RES  = arquivos do programa (pagina, gsap, versao). No .exe e a pasta interna do empacotador.
#   DATA = o que e da pessoa (config com chaves, contexto, notas, transcricoes). No .exe fica em
#          %LOCALAPPDATA%\TradutorDeCall, pra atualizar o programa sem apagar nada dela.
FROZEN = bool(getattr(sys, "frozen", False))
RES = getattr(sys, "_MEIPASS", HERE)
DATA = os.environ.get("TRADUTOR_DADOS") or (
    os.path.join(os.environ.get("LOCALAPPDATA") or HERE, "TradutorDeCall") if FROZEN else HERE)
os.makedirs(DATA, exist_ok=True)
# arquivos da placa NVIDIA baixados na 1a abertura (quem instalou pelo .exe)
for _d in glob.glob(os.path.join(DATA, "nvidia", "*", "bin")):
    try:
        os.add_dll_directory(_d)
        os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
    except OSError:
        pass
LOGDIR = os.path.join(DATA, "transcricoes")
os.makedirs(LOGDIR, exist_ok=True)


def read_user_file(*names):
    """Le o primeiro arquivo que existir: primeiro na pasta da pessoa, depois junto do programa."""
    for base in (DATA, RES):
        for n in names:
            try:
                with open(os.path.join(base, n), encoding="utf8") as f:
                    t = f.read().strip()
                if t:
                    return t
            except Exception:
                pass
    return ""


try:
    with open(os.path.join(RES, "versao.txt"), encoding="utf8") as _f:
        VERSION = _f.read().strip() or "0.0.0"
except Exception:
    VERSION = "0.0.0"

CFG = {
    "whisper": os.environ.get("TRADUTOR_MODELO", "large-v3-turbo"),
    "threshold": 0.004,      # RMS para considerar que alguem esta falando
    "silence_end": 0.45,     # silencio que fecha a frase (s)
    "max_len": 9.0,          # frase maxima antes de cortar (s)
    "partial_every": 0.25,    # previa a cada (s); na GPU da pra ser mais rapido
}
# velocidade da transcricao, escolhida em Ajustes (LAST 22/11: "opcao pra pessoa escolher; tempo real = muito rapido")
#   prev = previa a cada (s) | min = fala minima antes da 1a previa (s) | fim = silencio que fecha a frase (s)
#   beam = capricho da frase final | det = conferir a lingua: none / smart / always | hist = usar falas anteriores como contexto
SPEEDS = {
    "real":   {"prev": 0.15, "min": 0.4, "fim": 0.30, "beam": 1, "det": "none",   "hist": False},
    "equil":  {"prev": 0.25, "min": 0.8, "fim": 0.45, "beam": 2, "det": "smart",  "hist": True},
    "preciso": {"prev": 0.4, "min": 1.2, "fim": 0.55, "beam": 5, "det": "always", "hist": True},
}


def speed():
    return SPEEDS.get(state.get("speed"), SPEEDS["equil"])


# nomes e termos que o Whisper deve reconhecer nas calls de trabalho: vem do arquivo termos.txt
# (um texto corrido em ingles com nomes do time, produtos, jargao). Sem o arquivo, fica vazio.
PROMPT = read_user_file("termos.txt", "termos_padrao.txt")
STOCK_HALLUCINATIONS = ("for more information", "thanks for watching", "thank you for watching", "subscribe",
                        "see you next time", "see you in the next", "please like", "captions by", "subtitles by")
HALLUCINATIONS = {"you", "thank you.", "thanks for watching!", "thank you", "bye.", ".", "so", "okay.",
                  "mm.", "mm", "mm-hmm.", "hmm.", "hmm", "uh.", "um.", "ah.", "oh.", "...", "you."}

state = {
    "paused": True,   # abre sem captar; so comeca quando o LAST clicar (pedido 22/11)
    "device": None,          # nome do dispositivo de saida (loopback)
    "device_changed": threading.Event(),
    "resume": threading.Event(),   # captacao desligada: as saidas de audio ficam fechadas ate ligar (27/11, CPU)
    "level": 0.0,
}
clients = []
clients_lock = threading.Lock()
history = []
logfile = os.path.join(LOGDIR, datetime.now().strftime("call_%Y-%m-%d_%H-%M.txt"))


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def broadcast(ev):
    data = ("data: " + json.dumps(ev, ensure_ascii=False) + "\n\n").encode("utf8")
    with clients_lock:
        for q in list(clients):
            try:
                q.put_nowait(data)
            except queue.Full:
                pass


# ---------------------------------------------------------------- traducao
class MT:
    def __init__(self, repo):
        import ctranslate2
        import sentencepiece as spm
        from huggingface_hub import snapshot_download
        p = snapshot_download(repo)
        self.tr = ctranslate2.Translator(p, device="cpu", compute_type="int8", inter_threads=1)
        self.src = spm.SentencePieceProcessor(model_file=os.path.join(p, "source.spm"))
        self.tgt = spm.SentencePieceProcessor(model_file=os.path.join(p, "target.spm"))
        self.lock = threading.Lock()

    def __call__(self, text, beam=2):
        text = text.strip()
        if not text:
            return ""
        parts = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        with self.lock:
            batch = [self.src.encode(s, out_type=str) + ["</s>"] for s in parts]
            res = self.tr.translate_batch(batch, beam_size=beam, max_decoding_length=200)
        return " ".join(self.tgt.decode(r.hypotheses[0]) for r in res)


PT_BR = [  # o modelo as vezes escreve em portugues de Portugal
    (r"\bEstás\b", "Você está"), (r"\bestás\b", "você está"), (r"\btens\b", "você tem"),
    (r"\bTens\b", "Você tem"), (r"\bpodes\b", "você pode"), (r"\bPodes\b", "Você pode"),
    (r"\bqueres\b", "você quer"), (r"\bQueres\b", "Você quer"), (r"\bsabes\b", "você sabe"),
    (r"\bSabes\b", "Você sabe"), (r"\bvais\b", "vai"), (r"\bVais\b", "Vai"),
    (r"\bfazes\b", "faz"), (r"\bécrã\b", "tela"), (r"\becrã\b", "tela"), (r"\bequipa\b", "equipe"),
    (r"\btelemóvel\b", "celular"), (r"\bregisto\b", "registro"), (r"\bfacto\b", "fato"),
    (r"\bestá bem\b", "beleza"), (r"\bEstá bem\b", "Beleza"), (r"\bbom aspecto\b", "bonito"),
    (r"\bOlá\b", "Oi"), (r"\bcontigo\b", "com você"), (r"\bteu\b", "seu"), (r"\btua\b", "sua"),
    (r"\bTeu\b", "Seu"), (r"\bTua\b", "Sua"), (r"\bmiúdos\b", "crianças"), (r"\bganchos\b", "hooks"),
    (r"\bganchos\b", "hooks"), (r"\bgancho\b", "hook"), (r"\blote\b", "batch"),
]


def pt_br(s):
    for a, b in PT_BR:
        s = re.sub(a, b, s)
    return s


SLANG = [
    (r"\bto be honest\b", "tbh"), (r"\bnot gonna lie\b", "ngl"), (r"\blet me know\b", "lmk"),
    (r"\bright now\b", "rn"), (r"\bi am going to\b", "ima"), (r"\bi'm going to\b", "ima"),
    (r"\bgoing to\b", "gonna"), (r"\bwant to\b", "wanna"), (r"\bgot to\b", "gotta"),
    (r"\bbecause\b", "bc"), (r"\bcause\b", "bc"), (r"\babout\b", "bout"),
    (r"\byou are\b", "ur"), (r"\byou're\b", "ur"), (r"\byour\b", "ur"), (r"\byou\b", "u"),
    (r"\bwith\b", "w/"), (r"\bokay\b", "ok"), (r"\bi don't know\b", "idk"),
    (r"\bthanks\b", "ty"), (r"\bthank u\b", "ty"), (r"\bplease\b", "pls"),
    (r"\ball right\b", "aight"), (r"\balright\b", "aight"), (r"\bone\b", "1"), (r"\btwo\b", "2"),
    (r"\bthree\b", "3"), (r"\bfour\b", "4"), (r"\bfive\b", "5"), (r"\bminutes\b", "mins"),
    (r"\bfor real\b", "fr"), (r"\btonight\b", "tn"), (r"\bversion\b", "v"),
]


def informal(en):
    s = en.strip().lower()
    for a, b in SLANG:
        s = re.sub(a, b, s)
    s = re.sub(r"[.!]+(\s|$)", r"\1", s)      # tira ponto final e exclamacao
    s = re.sub(r",", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ---------------------------------------------------------------- sugestoes de resposta
# Qual IA sugere respostas: "off" (desligado), "gemini" ou "ollama". Fica em config.json.
CONF_PATH = os.path.join(DATA, "config.json")
PAD_PATH = os.path.join(DATA, "bloco_de_notas.txt")


def load_conf():
    try:
        with open(CONF_PATH, encoding="utf8") as f:
            return json.load(f)
    except Exception:
        return {"ia": "off", "ollama_model": "qwen2.5:3b", "gemini_model": "gemini-3.5-flash-lite"}


SUGGEST_PROMPT = """You help {me} during a live Discord call {who}. \
Use this background (use the right names and terms, never contradict it):

{crown}

Below is the latest part of the call (what the others said, transcribed, may have small errors; \
fix obvious misheard names/terms using the background).

{ctx}

Write 3 different short replies {me} could say or type right now to the last line above, each max 14 words. \
Make them useful and different: e.g. confirm/agree, ask a quick clarifying question, give a short update or next step. \
Sound like a fluent young native on Discord: casual, lowercase, slang/abbrevs where natural (u, ur, rn, bet, gonna, ima, \
ngl, tbh, lmk, bc, w/, bout, fr), no formal punctuation. Never invent specific facts or numbers. \
Also give a natural Brazilian Portuguese translation of each reply.
Return ONLY JSON: [{{"en": "...", "pt": "..."}}, ...]"""


class UserError(Exception):
    """Erro com mensagem pronta pra mostrar pro LAST na janela."""


# IAs que podem sugerir respostas. A chave vem da variavel de ambiente ou do que o LAST colar em Ajustes
# (fica so no config.json deste PC; a pagina nunca recebe a chave de volta).
PROVIDERS = {
    "gemini": {"nome": "Gemini", "env": "GEMINI_API_KEY", "model": "gemini-3.5-flash-lite"},
    "openai": {"nome": "ChatGPT", "env": "OPENAI_API_KEY", "model": "gpt-5-mini"},
    "claude": {"nome": "Claude", "env": "ANTHROPIC_API_KEY", "model": "claude-haiku-5-5"},
    "grok": {"nome": "Grok", "env": "XAI_API_KEY", "model": "grok-4.3"},
}
OPENAI_LIKE = {"openai": "https://api.openai.com/v1/chat/completions",
               "grok": "https://api.x.ai/v1/chat/completions"}


def ia_key(conf, ia):
    return (conf.get(ia + "_key") or os.environ.get(PROVIDERS[ia]["env"]) or "").strip()


def ia_status(conf=None):
    conf = conf or load_conf()
    return {"ia": conf.get("ia", "off"), "tradutor": conf.get("tradutor", "ia"), "trad_ia": trad_provider(conf),
            "ias": [{"id": k, "nome": p["nome"], "tem_chave": bool(ia_key(conf, k)),
                     "model": conf.get(k + "_model") or p["model"]} for k, p in PROVIDERS.items()]}


def save_conf(conf):
    with open(CONF_PATH, "w", encoding="utf8") as f:
        json.dump(conf, f, indent=2, ensure_ascii=False)


def _post_json(url, body, headers=None, timeout=20):
    import urllib.request
    req = urllib.request.Request(url, json.dumps(body).encode("utf8"),
                                 {"Content-Type": "application/json", **(headers or {})})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def suggest(lines):
    conf = load_conf()
    ia = conf.get("ia", "off")
    if ia == "off" or not lines:
        return {"off": True, "items": []}
    ctx = "\n".join(f"- {l}" for l in lines[-8:])
    me = my_name(conf)
    if conf.get("contexto", "crown") == "comum":   # conversa comum: nada de termos de trabalho
        who = "with friends or people they are chatting with (casual conversation, not work)"
        crown = (f"{me} is Brazilian, friendly and relaxed. Their English is good for reading "
                 "but they need help building replies.")
    else:
        # contexto de trabalho: o arquivo da pessoa (contexto.md) ou, sem ele, o do nicho que vem com o programa
        who = "with their work team"
        crown = read_user_file("contexto.md", "contexto_crown.md", "contexto_padrao.md") or (
            f"{me} is Brazilian and works with video ads. Their English is good for reading "
            "but they need help building replies.")
    notas = (conf.get("notas") or "").strip()
    if notas:
        crown += f"\n\n## About this specific call ({me}'s notes, may be in Portuguese)\n" + notas
    prompt = SUGGEST_PROMPT.format(ctx=ctx, crown=crown, who=who, me=me)
    data = ask_ia(prompt, conf)
    items = [{"en": informal(str(d.get("en", ""))), "pt": str(d.get("pt", ""))}
             for d in data if isinstance(d, dict) and d.get("en")][:3]
    last_sugs[:] = items
    return {"off": False, "items": items}


def my_name(conf=None):
    """Como a IA chama a pessoa (config "nome"). Sem nome, fica "the user"."""
    return ((conf or load_conf()).get("nome") or "").strip()[:40] or "the user"


TOPICS_PROMPT = """You are taking notes for {me} (a Brazilian) during a live call {who}.
Notes already written (do not repeat anything that is already here):
{notes}

New lines of the call (English, transcribed live, may contain small errors):
{ctx}

Write 0 to 3 short notes in Brazilian Portuguese, max 14 words each, with ONLY new and concrete information: \
decisions, tasks and who does them, deadlines, numbers, names, links, questions asked to {me}. \
Skip small talk, greetings, jokes and anything already in the notes. If nothing is worth noting, return [].
Return ONLY a JSON list of strings."""


def topics(since_id):
    """Topicos novos da call pro bloco de notas (LAST 22/11: "ele vai anotando sozinho")."""
    conf = load_conf()
    lines = [h for h in history if h["id"] > since_id and not h.get("mine")]
    last = lines[-1]["id"] if lines else since_id
    if conf.get("ia", "off") == "off":
        return {"off": True, "items": [], "last": since_id}
    if len(lines) < 2:
        return {"items": [], "last": since_id}
    try:
        with open(PAD_PATH, encoding="utf8") as f:
            notes = f.read()[-1500:].strip() or "(empty)"
    except Exception:
        notes = "(empty)"
    who = ("with friends (casual conversation)" if conf.get("contexto", "crown") == "comum"
           else "with their work team")
    data = ask_ia(TOPICS_PROMPT.format(who=who, notes=notes, me=my_name(conf),
                                       ctx="\n".join(f"- {h['en']}" for h in lines[-25:])), conf)
    items = [str(x.get("pt") or x.get("note") or "") if isinstance(x, dict) else str(x) for x in data]
    return {"items": [i.strip() for i in items if i.strip()][:3], "last": last}


def ask_ia(prompt, conf=None, temperature=None):
    """Manda o texto pra IA escolhida em Ajustes e devolve a lista que ela respondeu (JSON)."""
    conf = conf or load_conf()
    ia = conf.get("ia", "off")
    if ia in ("openai", "claude", "grok"):
        p = PROVIDERS[ia]
        key = ia_key(conf, ia)
        if not key:
            raise UserError(f"Falta a chave da API do {p['nome']}. Cola ela em Ajustes.")
        model = conf.get(ia + "_model") or p["model"]
        import urllib.error
        try:
            if ia in OPENAI_LIKE:   # o Grok (xAI) usa o mesmo formato do ChatGPT
                r = _post_json(OPENAI_LIKE[ia],
                               {"model": model, "messages": [{"role": "user", "content": prompt}]},
                               {"Authorization": "Bearer " + key}, timeout=20)
                txt = r["choices"][0]["message"]["content"]
            else:
                r = _post_json("https://api.anthropic.com/v1/messages",
                               {"model": model, "max_tokens": 500, "messages": [{"role": "user", "content": prompt}]},
                               {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=20)
                txt = "".join(b.get("text", "") for b in r["content"] if b.get("type") == "text")
        except urllib.error.HTTPError as e:
            log(ia, model, "falhou:", e.code)
            if e.code in (401, 403):
                raise UserError(f"O {p['nome']} recusou a chave. Confere ela em Ajustes.")
            if e.code == 404:
                raise UserError(f"O {p['nome']} não achou o modelo \"{model}\". Troca o nome em Ajustes.")
            if e.code == 429:
                raise UserError(f"O {p['nome']} disse que acabou o limite ou o crédito da conta.")
            raise
    elif ia == "gemini":
        import urllib.error
        key = ia_key(conf, "gemini")
        if not key:
            raise UserError("Falta a chave da API do Gemini. Cola ela em Ajustes.")
        models = [conf.get("gemini_model", "gemini-3.5-flash-lite"), "gemini-flash-lite-latest", "gemini-3.1-flash-lite"]
        last_err = None
        for model in dict.fromkeys(models):  # se um estiver lotado (503), tenta o proximo
            gc = {"responseMimeType": "application/json", "temperature": 0.8 if temperature is None else temperature}
            if model.startswith("gemini-3"):
                gc["thinkingConfig"] = {"thinkingLevel": "minimal"}
            try:
                r = _post_json(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gc},
                    {"x-goog-api-key": key}, timeout=12)
                txt = r["candidates"][0]["content"]["parts"][0]["text"]
                break
            except urllib.error.HTTPError as e:
                last_err = e
                log("gemini", model, "falhou:", e.code)
                if e.code in (401, 403):
                    raise
        else:
            raise last_err
    else:
        r = _post_json("http://localhost:11434/api/chat",
                       {"model": conf.get("ollama_model", "qwen2.5:3b"), "stream": False, "format": "json",
                        "options": {"temperature": 0.7, "num_predict": 220},
                        "messages": [{"role": "user", "content": prompt}]}, timeout=40)
        txt = r["message"]["content"]
    # alguns modelos embrulham o JSON em texto ou em ```: pega so o miolo
    a = min([i for i in (txt.find("["), txt.find("{")) if i >= 0] or [0])
    b = max(txt.rfind("]"), txt.rfind("}")) + 1
    data = json.loads(txt[a:b] if b > a else txt)
    if isinstance(data, dict):  # alguns modelos embrulham a lista
        data = next((v for v in data.values() if isinstance(v, list)), [])
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------- traducao boa da frase fechada
# (27/11, amigos do LAST acharam a traducao "muito ruim"; LAST: "melhora sim o motor"). Comparado em call real:
# o opus-mt local traduz palavra por palavra ("like" -> "como", "wrap it up" -> "envolver", "blast it" -> "explodi-lo");
# a IA entende girias e o contexto em ~0,7 s. Entao: a previa ao vivo continua local (instantanea) e a frase
# fechada e trocada pela da IA logo depois. Usa a chave de IA que a pessoa ja colou (a do Gemini e gratis).
# Sem chave, ou em Ajustes -> "Motor da traducao: Offline", fica a local.
TRANSLATE_PROMPT = """Translate one line from a live {kind} into natural Brazilian Portuguese, the way a Brazilian \
would actually say it. Casual, never Portugal Portuguese. It was transcribed live: no punctuation, fillers \
(um, uh, like, you know) and it may start or end mid-sentence. Drop meaningless fillers, translate idioms and slang \
by meaning (not word by word), keep names, brands and jargon as they are. Do not add anything that is not in the line.{terms}
Previous lines (context only, do NOT translate them):
{prev}
Line to translate: {line}
Return ONLY a JSON list with one string: the translation."""


def trad_provider(conf):
    """Qual IA traduz: a escolhida em Ajustes, ou qualquer uma que tenha chave. None = traducao local."""
    if conf.get("tradutor", "ia") == "local":
        return None
    ia = conf.get("ia", "off")
    if ia in PROVIDERS and ia_key(conf, ia):
        return ia
    return next((k for k in PROVIDERS if ia_key(conf, k)), None)


def better_pt(en, prev=(), conf=None):
    """Traducao boa de uma frase em ingles pela IA (ou None: sem chave, desligado ou falhou)."""
    conf = conf or load_conf()
    ia = trad_provider(conf)
    if not ia:
        return None
    work = conf.get("contexto", "crown") != "comum"
    terms = (" Names and terms used in this call: " + PROMPT[:500]) if work and PROMPT else ""
    prompt = TRANSLATE_PROMPT.format(kind="work call" if work else "conversation between friends", terms=terms,
                                     prev="\n".join(f"- {p}" for p in prev) or "(none)", line=en)
    try:
        data = ask_ia(prompt, {**conf, "ia": ia}, temperature=0.2)
        pt = next((str(d).strip() for d in data if isinstance(d, str) and str(d).strip()), "")
        return {"pt": pt, "via": ia} if pt else None
    except Exception as e:
        log("traducao pela IA falhou:", repr(e)[:150])
        return None


def fix_pt(item, prev, logfile):
    """Roda numa thread depois que a frase aparece: troca a traducao local pela da IA e avisa a janela."""
    t0 = time.time()
    try:
        r = better_pt(item["en"], prev)
        if r and r["pt"] != item["pt"]:
            log(f"traducao {r['via']} em {time.time() - t0:.1f}s:", r["pt"])
            item["pt"] = r["pt"]
            broadcast({"type": "pt_fix", "id": item["id"], "pt": r["pt"]})
    finally:
        try:
            with open(logfile, "a", encoding="utf8") as f:
                f.write(f"[{item['t']}] {item['en']}\n           {item['pt']}\n\n")
        except Exception:
            pass


last_sugs = []   # ultimas sugestoes (pros atalhos Ctrl+Shift+1/2/3)


auto_timer = {"t": None}


def auto_suggest(item):
    """Depois de cada fala dos outros, ja manda 3 respostas prontas (sem clicar)."""
    if item.get("mine") or load_conf().get("ia", "off") == "off":
        return
    if auto_timer["t"]:
        auto_timer["t"].cancel()

    def run():
        broadcast({"type": "sugs_wait", "id": item["id"]})
        try:
            lines = [h["en"] for h in history if h["id"] <= item["id"] and not h.get("mine")]
            r = suggest(lines)
            item["sugs"] = r.get("items", [])
            broadcast({"type": "sugs", "id": item["id"], "items": r.get("items", [])})
        except Exception as e:
            log("sugestao automatica falhou:", repr(e)[:150])
            broadcast({"type": "sugs", "id": item["id"], "items": [], "error": "Sem sugestões agora (IA ocupada)."})

    delay = 0.05 if item["en"].rstrip().endswith("?") else 0.6
    auto_timer["t"] = threading.Timer(delay, run)
    auto_timer["t"].daemon = True
    auto_timer["t"].start()


def set_paused(v):
    state["paused"] = bool(v)
    (state["resume"].clear if state["paused"] else state["resume"].set)()
    broadcast({"type": "paused", "v": state["paused"]})


# ---------------------------------------------------------------- audio
def loopback_names():
    import soundcard as sc
    return [m.name for m in sc.all_microphones(include_loopback=True) if m.isloopback]


def mic_names():
    import soundcard as sc
    return [m.name for m in sc.all_microphones(include_loopback=False)]


def mic_mode():
    return (state["device"] or "").startswith("mic:")


AUTO = "auto"
energy = {}        # nome -> rms recente de cada saida
active = {"name": None}


def device_thread(name, out_q, gen):
    """Grava uma saida de audio (loopback) e manda os blocos com o nome dela."""
    import soundcard as sc
    while gen == state["gen"]:
        if state["paused"]:   # desligado: nao grava nada (antes gastava ~15% de um nucleo parado)
            state["resume"].wait(1.0)
            continue
        try:
            if name.startswith("mic:"):  # microfone do LAST (pra testar / falar)
                mic = sc.get_microphone(name[4:], include_loopback=False)
            else:
                mic = sc.get_microphone(name, include_loopback=True)
            with mic.recorder(samplerate=SR, channels=1, blocksize=BLOCK) as rec:
                while gen == state["gen"] and not state["paused"]:
                    d = rec.record(numframes=BLOCK).reshape(-1).astype(np.float32)
                    rms = float(np.sqrt(np.mean(d * d))) if d.size else 0.0
                    energy[name] = energy.get(name, 0.0) * 0.8 + rms * 0.2
                    out_q.put((name, d))
        except Exception as e:
            log("erro no audio", name, repr(e))
            time.sleep(3)


def capture_loop(out_q):
    """Modo auto: ouve todas as saidas e usa a que estiver tocando voz (fone ou caixa)."""
    import soundcard as sc
    state["gen"] = 0
    while True:
        state["gen"] += 1
        gen = state["gen"]
        state["device_changed"].clear()
        energy.clear()
        want = state["device"] or AUTO
        names = loopback_names() if want == AUTO else [want]
        try:
            default = sc.default_speaker().name
        except Exception:
            default = names[0] if names else None
        active["name"] = default if want == AUTO else want
        log("ouvindo:", ", ".join(names), "| modo:", want)
        broadcast({"type": "status", "device": want})
        for n in names:
            threading.Thread(target=device_thread, args=(n, out_q, gen), daemon=True).start()
        state["device_changed"].wait()


def pick_block(name, d):
    """Devolve o bloco so se ele vier da saida ativa; troca de saida quando outra comeca a tocar."""
    cur = active["name"]
    if name != cur:
        th = CFG["threshold"]
        if energy.get(name, 0) > th and energy.get(cur, 0) < th * 0.5:
            active["name"] = name
            log("trocando para:", name)
            broadcast({"type": "active", "device": name})
            return d
        return None
    return d


# ---------------------------------------------------------------- transcricao
def main_loop(in_q, model, en2pt):
    buf = []          # blocos da frase atual
    pre = []          # 0,3 s antes da fala
    silent = 0.0
    speaking = False
    last_partial = 0.0
    seg_id = 0
    last_level_sent = 0.0

    lang_cache = {}   # seg_id -> hora da ultima previa que deu ingles
    not_en = set()    # falas em que alguma previa ouviu outra lingua
    can_detect = bool(getattr(model.model, "is_multilingual", True))   # small.en (reserva na CPU) so sabe ingles

    def recent_text():
        """Ultimas falas em ingles (ate ~200 letras, dos ultimos 40 s) pra dar contexto ao Whisper."""
        now = time.time()
        txt = " ".join(h["en"] for h in history[-3:] if not h.get("mine") and now - h.get("ts", 0) < 40)
        return txt[-200:]

    def english_only(audio):
        """Devolve (audio so com os trechos em ingles, outra lingua ouvida). (None, lingua) se nada for ingles.
        Corta a fala em ate 3 pedacos, no ponto mais silencioso perto de cada divisao, e detecta a lingua de cada um."""
        dur = len(audio) / SR
        # velocidade (LAST 22/11, "muito lento"): olha a frase inteira primeiro; se for ingles com certeza,
        # nao corta em pedacos (cada deteccao custa uma passada inteira do Whisper na placa)
        l, p, _ = model.detect_language(audio)
        log(f"lingua da frase toda ({dur:.1f}s): {l} {p:.2f}")
        if l == "en" and p >= 0.9:
            return audio, None
        if dur < 3.5:
            return (audio, None) if (l == "en" and p >= 0.85) else (None, l if l != "en" else "?")
        k = 2 if dur < 7 else 3
        cuts = [0]
        if k > 1:
            fr = SR // 10
            n = len(audio) // fr
            rms = np.sqrt((audio[:n * fr].reshape(n, fr) ** 2).mean(1))
            for i in range(1, k):
                c = n * i // k
                lo, hi = max(cuts[-1] // fr + 12, c - 7), min(n - 12, c + 7)
                if hi > lo:
                    cuts.append((lo + int(np.argmin(rms[lo:hi]))) * fr)
        cuts.append(len(audio))
        keep, other = [], None
        for a, b in zip(cuts, cuts[1:]):
            l, p, _ = model.detect_language(audio[a:b])
            log(f"lingua do trecho {a / SR:.1f}-{b / SR:.1f}s: {l} {p:.2f}")
            if l == "en" and p >= 0.85:
                keep.append(audio[a:b])
            elif l != "en":
                other = other or l
        if not keep:
            return None, other or "?"
        if other:
            log(f"fala misturada: fiquei com {len(keep)} de {len(cuts) - 1} trechos (resto em {other})")
        return np.concatenate(keep), other

    def transcribe(audio, final):
        """Devolve (texto, idioma). No modo microfone detecta PT ou EN sozinho."""
        # filtro "so ingles" (LAST 22/11): o Whisper detecta a lingua da frase; se nao for ingles, ignora
        # (ex.: call com amigos em PT-BR). No microfone nao vale: la ele fala PT pra virar ingles.
        sp = speed()
        only_en = state.get("only_en", True) and not mic_mode() and can_detect
        lang = None if mic_mode() else "en"
        if only_en:
            if not final:
                if len(audio) / SR < sp["min"]:
                    return "", "?"      # curto demais pra saber a lingua: espera mais um pouco
                # previa: olha so o fim da fala; se a pessoa passou pro portugues, para de mostrar.
                # velocidade: se essa fala ja deu ingles ha menos de 1,5 s, nao detecta de novo.
                # no modo Tempo real nao confere a lingua (so a regua de certeza la embaixo)
                lc = lang_cache.get(seg_id)
                if sp["det"] != "none" and not (lc and time.time() - lc < 1.5):
                    l, p, _ = model.detect_language(audio[-int(2.5 * SR):])
                    if l != "en" or p < 0.85:
                        lang_cache.pop(seg_id, None)
                        not_en.add(seg_id)
                        return "", (l if l != "en" else "?")
                    lang_cache.clear(); lang_cache[seg_id] = time.time()
            else:
                # frase fechada: separa por trechos e fica so com o que e ingles
                # (LAST 22/11: "falou em ingles e depois em portugues; o portugues nao deve captar")
                # velocidade: se as previas dessa fala ja confirmaram ingles do comeco ao fim, nao detecta de novo
                if sp["det"] == "none":
                    pass
                elif sp["det"] == "smart" and seg_id in lang_cache and seg_id not in not_en and len(audio) / SR < 6:
                    log("lingua: previas ja deram ingles, sem nova deteccao")
                else:
                    audio, other = english_only(audio)
                if audio is None:
                    return "", other
        # contexto: nomes e termos de trabalho so em call de trabalho; em conversa comum eles atrapalham.
        # as ultimas falas entram como contexto pra manter nomes e assunto.
        prompt = (PROMPT + " " if state.get("ctx", "crown") == "crown" else "") + (recent_text() if sp["hist"] else "")
        segs, info = model.transcribe(
            audio, language=lang, beam_size=sp["beam"] if final else 1,
            condition_on_previous_text=False, initial_prompt=prompt.strip() or None,
            vad_filter=final, no_speech_threshold=0.6, without_timestamps=True,
        )
        out = []
        # no filtro "so ingles" a regua e mais dura: portugues, risada ou musica forcados a virar ingles saem com
        # pouca certeza (logprob baixo) ou repetindo frase ("I can't wait" x5, visto na call do LAST em 22/11)
        max_nsp, min_lp, max_cr = (0.5, -0.7, 1.9) if only_en else (0.7, -1.2, 9.0)
        seen = []
        for s in segs:
            seen.append((round(s.avg_logprob, 2), round(s.no_speech_prob, 2), round(s.compression_ratio, 2)))
            if s.no_speech_prob > max_nsp or s.avg_logprob < min_lp or s.compression_ratio > max_cr:
                continue
            out.append(s.text.strip())
        txt = " ".join(out).strip()
        det = lang or (info.language if info.language in ("pt", "en") else "en")
        if final and only_en and seen and not txt:
            log("descartado por pouca certeza (logprob, sem-fala, repeticao):", seen)
        low = txt.lower()
        if low in HALLUCINATIONS:
            return "", det
        if only_en and txt:
            # frases que o Whisper inventa em cima de ruido
            if len(txt.split()) < 9 and any(h in low for h in STOCK_HALLUCINATIONS):
                return "", "?"
            # mesma frase 3+ vezes = invencao
            sents = [x.strip().lower() for x in re.split(r"[.!?]+", txt) if x.strip()]
            if sents and max(sents.count(x) for x in set(sents)) >= 3:
                log("descartado por repeticao:", txt[:80])
                return "", "?"
            # so age com frase de verdade: palavra solta ("yeah", "perfect") em call em portugues
            # costuma ser erro de deteccao
            if final and len(txt.split()) < 3:   # na previa deixa passar, pra fala aparecer desde a 1a palavra
                return "", "?"
            if final:
                log("aceito (logprob, sem-fala, repeticao):", seen)
        return txt, det

    def both(txt, det, beam=2):
        """(en, pt) da fala, venha ela em ingles ou em portugues."""
        if det == "pt":
            return informal(pt2en(txt, beam)), txt
        return txt, en2pt(txt, beam)

    while True:
        raw = [in_q.get()]
        # junta o que estiver acumulado (se a transcricao atrasou)
        while not in_q.empty():
            raw.append(in_q.get())
        blocks = [b for b in (pick_block(n, d) for n, d in raw) if b is not None]
        if not blocks:
            continue
        for b in blocks:
            rms = float(np.sqrt(np.mean(b * b))) if b.size else 0.0
            state["level"] = rms
            if state["paused"]:
                buf, pre, speaking, silent = [], [], False, 0.0
                continue
            th = max(CFG["threshold"], 0.012) if mic_mode() else CFG["threshold"]
            if rms > th:
                if not speaking:
                    speaking = True
                    buf = list(pre)
                    seg_id += 1
                    last_partial = time.time()
                silent = 0.0
                buf.append(b)
            elif speaking:
                buf.append(b)
                silent += len(b) / SR
            else:
                pre.append(b)
                pre = pre[-3:]

        now = time.time()
        if now - last_level_sent > 0.1 and not state["paused"]:   # desligado: nao manda nivel (a janela fica parada)
            broadcast({"type": "level", "v": round(min(1.0, state["level"] / 0.05), 3)})
            last_level_sent = now

        if not speaking:
            continue
        dur = sum(len(x) for x in buf) / SR
        sp = speed()
        fim = sp["fim"]
        prev_every = CFG["partial_every"] if CFG["partial_every"] > 1 else sp["prev"]   # na CPU fica o lento
        end = silent >= fim or dur >= CFG["max_len"]
        if end:
            audio = np.concatenate(buf)
            speaking, silent, buf = False, 0.0, []
            if dur - fim < 0.4:
                broadcast({"type": "drop", "id": seg_id})
                continue
            t0 = time.time()
            txt, det = transcribe(audio, True)
            if not txt:
                broadcast({"type": "drop", "id": seg_id})
                if det not in ("en", "pt", "?") or (det == "pt" and not mic_mode()):
                    broadcast({"type": "skip", "lang": det})   # a janela avisa "ignorando fala em ..."
                continue
            en, pt = both(txt, det)
            item = {"type": "final", "id": seg_id, "en": en, "pt": pt, "mine": mic_mode(), "lang": det, "ts": time.time(),
                    "t": datetime.now().strftime("%H:%M:%S")}
            history.append(item)
            del history[:-300]
            broadcast(item)
            # sugestoes so quando o LAST clicar (pedido 22/11)
            # traducao da IA por cima da local, sem segurar a fala na tela
            if det == "en" and not item["mine"]:
                prev = [h["en"] for h in history[-5:-1] if not h.get("mine")][-3:]
                threading.Thread(target=fix_pt, args=(item, prev, logfile), daemon=True).start()
            else:
                with open(logfile, "a", encoding="utf8") as f:
                    f.write(f"[{item['t']}] {en}\n           {pt}\n\n")
            log(f"{time.time() - t0:.1f}s |", en, "=>", pt)
        elif now - last_partial >= prev_every and dur > min(0.5, sp["min"]):
            last_partial = now
            txt, det = transcribe(np.concatenate(buf), False)
            if txt:
                en, pt = both(txt, det, beam=1)
                broadcast({"type": "partial", "id": seg_id, "en": en, "pt": pt, "lang": det})


# ---------------------------------------------------------------- web
def PAGE():
    with open(os.path.join(RES, "pagina.html"), encoding="utf8") as f:
        html = f.read()
    # aparencia escolhida (cor, fundo, vidro, letra...) salva no config.json; entra antes de tudo pra nao piscar
    prefs = json.dumps(load_conf().get("aparencia") or {}, ensure_ascii=False).replace("</", "<\\/")
    return html.replace("<head>", f"<head><script>window.__PREFS={prefs};</script>", 1)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            b = PAGE().encode("utf8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif self.path.startswith("/fonts/") and re.fullmatch(r"/fonts/[\w.-]+\.(css|woff2)", self.path):
            fp = os.path.join(RES, "fonts", self.path[7:])
            if not os.path.isfile(fp):
                return self.send_error(404)
            b = open(fp, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8" if fp.endswith(".css") else "font/woff2")
            self.send_header("Cache-Control", "max-age=604800")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif self.path == "/gsap.min.js":
            b = open(os.path.join(RES, "gsap.min.js"), "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Cache-Control", "max-age=86400")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q = queue.Queue(maxsize=500)
            with clients_lock:
                clients.append(q)
            try:
                hello = {"type": "hello", "history": history[-80:], "device": state["device"],
                         "paused": state["paused"], "log": logfile}
                self.wfile.write(("data: " + json.dumps(hello, ensure_ascii=False) + "\n\n").encode("utf8"))
                self.wfile.flush()
                while True:
                    try:
                        data = q.get(timeout=15)
                    except queue.Empty:
                        data = b": ping\n\n"
                    self.wfile.write(data)
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                with clients_lock:
                    if q in clients:
                        clients.remove(q)
        elif self.path == "/config":
            c = load_conf()
            self._json({**ia_status(c), "notas": c.get("notas", ""), "only_en": state.get("only_en", True),
                        "ctx": state.get("ctx", "crown"), "speed": state.get("speed", "equil")})
        elif self.path == "/update":   # tem versao nova no GitHub?
            self._json(check_update())
        elif self.path == "/perfil":   # nome, contexto fixo e termos (cada pessoa edita o seu em Ajustes)
            self._json({"nome": load_conf().get("nome", ""), "termos": PROMPT,
                        "contexto": read_user_file("contexto.md", "contexto_crown.md", "contexto_padrao.md")})
        elif self.path == "/pad":   # bloco de notas embutido (fica em bloco_de_notas.txt)
            try:
                with open(PAD_PATH, encoding="utf8") as f:
                    self._json({"text": f.read()})
            except Exception:
                self._json({"text": ""})
        elif self.path == "/devices":
            try:
                import soundcard as sc
                self._json({"devices": loopback_names(), "mics": mic_names(), "default": sc.default_speaker().name,
                            "current": state["device"] or AUTO, "active": active["name"]})
            except Exception as e:
                self._json({"devices": [], "error": str(e)})
        else:
            self.send_error(404)

    def do_POST(self):
        body = self._body()
        if self.path == "/reply":
            pt = (body.get("text") or "").strip()
            en = pt2en(pt) if pt else ""
            self._json({"en": en, "informal": informal(en)})
        elif self.path == "/suggest":
            upto = body.get("id")
            lines = [h["en"] for h in history if upto is None or h["id"] <= upto]
            try:
                self._json(suggest(lines))
            except UserError as e:
                self._json({"error": str(e), "items": []})
            except Exception as e:
                log("sugestao falhou:", repr(e)[:200])
                self._json({"error": "Não deu pra gerar sugestões agora.", "items": []})
        elif self.path == "/perfil":  # salva nome / contexto fixo / termos na pasta da pessoa
            global PROMPT
            if "nome" in body:
                conf = load_conf()
                conf["nome"] = str(body.get("nome") or "").strip()[:40]
                save_conf(conf)
            if "contexto" in body:
                with open(os.path.join(DATA, "contexto.md"), "w", encoding="utf8") as f:
                    f.write(str(body.get("contexto") or "")[:20000])
            if "termos" in body:
                PROMPT = " ".join(str(body.get("termos") or "").split())[:1500]
                with open(os.path.join(DATA, "termos.txt"), "w", encoding="utf8") as f:
                    f.write(PROMPT)
            self._json({"ok": True})
        elif self.path == "/update":  # baixa o instalador novo e abre (o app fecha em seguida)
            try:
                self._json(do_update())
            except Exception as e:
                log("atualizacao falhou:", repr(e)[:200])
                self._json({"error": "Não deu pra baixar a atualização. Tenta de novo ou baixa pelo site."})
        elif self.path == "/topics":  # o bloco de notas pede os topicos novos da call
            try:
                self._json(topics(int(body.get("since") or 0)))
            except UserError as e:
                self._json({"error": str(e), "items": []})
            except Exception as e:
                log("topicos falharam:", repr(e)[:200])
                self._json({"error": "Não deu pra anotar agora.", "items": []})
        elif self.path == "/ia":  # o LAST liga/desliga as sugestoes no menu do app
            conf = load_conf()
            conf["ia"] = body.get("ia") if body.get("ia") in ("off", *PROVIDERS) else "off"
            save_conf(conf)
            broadcast({"type": "config", **ia_status(conf)})
            self._json(ia_status(conf))
        elif self.path == "/pad":
            with open(PAD_PATH, "w", encoding="utf8") as f:
                f.write(str(body.get("text") or "")[:200000])
            self._json({"ok": True})
        elif self.path == "/ctx":  # tipo de call: "crown" (= trabalho, usa contexto e termos) ou "comum"
            conf = load_conf()
            conf["contexto"] = state["ctx"] = "comum" if body.get("v") == "comum" else "crown"
            save_conf(conf)
            broadcast({"type": "ctx", "v": state["ctx"]})
            self._json({"ctx": state["ctx"]})
        elif self.path == "/speed":  # velocidade da transcricao: real / equil / preciso
            conf = load_conf()
            conf["velocidade"] = state["speed"] = body.get("v") if body.get("v") in SPEEDS else "equil"
            save_conf(conf)
            broadcast({"type": "speed", "v": state["speed"]})
            self._json({"speed": state["speed"]})
        elif self.path == "/only_en":  # captar so ingles (ignora outras linguas) ou qualquer lingua
            conf = load_conf()
            conf["so_ingles"] = state["only_en"] = bool(body.get("v"))
            save_conf(conf)
            broadcast({"type": "only_en", "v": state["only_en"]})
            self._json({"only_en": state["only_en"]})
        elif self.path == "/ia_key":  # o LAST cola a chave (ou troca o modelo) de uma IA em Ajustes
            conf = load_conf()
            ia = body.get("ia")
            if ia not in PROVIDERS:
                return self._json({"error": "IA desconhecida"})
            if "key" in body:
                k = str(body.get("key") or "").strip()[:300]
                if k:
                    conf[ia + "_key"] = k
                else:
                    conf.pop(ia + "_key", None)
            if "model" in body:
                m = str(body.get("model") or "").strip()[:80]
                if m:
                    conf[ia + "_model"] = m
                else:
                    conf.pop(ia + "_model", None)
            save_conf(conf)
            broadcast({"type": "config", **ia_status(conf)})
            self._json(ia_status(conf))
        elif self.path == "/tradutor":  # motor da traducao da frase pronta: "ia" (melhor) ou "local" (offline)
            conf = load_conf()
            conf["tradutor"] = "local" if body.get("v") == "local" else "ia"
            save_conf(conf)
            broadcast({"type": "config", **ia_status(conf)})
            self._json(ia_status(conf))
        elif self.path == "/prefs":  # aparencia que o LAST escolheu (fica salva mesmo fechando o app)
            if isinstance(body, dict) and len(json.dumps(body)) < 5000:
                conf = load_conf()
                conf["aparencia"] = body
                save_conf(conf)
            self._json({"ok": True})
        elif self.path == "/notes":
            conf = load_conf()
            conf["notas"] = str(body.get("text") or "")[:3000]
            with open(CONF_PATH, "w", encoding="utf8") as f:
                json.dump(conf, f, indent=2, ensure_ascii=False)
            self._json({"ok": True})
        elif self.path == "/pt":
            self._json({"pt": en2pt(body.get("text") or "")})
        elif self.path == "/device":
            state["device"] = body.get("device") or None
            state["device_changed"].set()
            self._json({"ok": True})
        elif self.path == "/pause":
            set_paused(body.get("paused"))
            self._json({"ok": True})
        elif self.path == "/reset":
            # apaga o chat atual e comeca uma call nova (o arquivo antigo fica salvo)
            global logfile
            history.clear()
            last_sugs.clear()
            logfile = os.path.join(LOGDIR, datetime.now().strftime("call_%Y-%m-%d_%H-%M-%S.txt"))
            log("chat zerado, nova transcricao em:", logfile)
            broadcast({"type": "reset"})
            self._json({"ok": True})
        elif self.path == "/sensitivity":
            CFG["threshold"] = float(body.get("v", CFG["threshold"]))
            self._json({"ok": True, "threshold": CFG["threshold"]})
        else:
            self.send_error(404)


# ---------------------------------------------------------------- atualizacao
REPO = "everLASTing-code/tradutor-de-call"     # onde ficam as versoes (aba Releases do GitHub)
_upd = {"t": 0, "r": None}


def _ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3]) or (0,)


def check_update():
    """Olha a ultima versao publicada. So leitura, sem login; guarda a resposta por 30 min."""
    if _upd["r"] and time.time() - _upd["t"] < 1800:
        return _upd["r"]
    r = {"versao": VERSION, "nova": None}
    try:
        import urllib.request
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "tradutor-de-call"})
        d = json.loads(urllib.request.urlopen(req, timeout=6).read())
        tag = d.get("tag_name") or ""
        if _ver(tag) > _ver(VERSION):
            exe = next((a for a in d.get("assets", []) if a.get("name", "").lower().endswith(".exe")), None)
            r.update(nova=tag.lstrip("v"), notas=(d.get("body") or "")[:600], pagina=d.get("html_url"),
                     url=exe and exe.get("browser_download_url"), tamanho=exe and exe.get("size"))
    except Exception as e:
        log("nao deu pra conferir atualizacao:", repr(e)[:120])
    _upd.update(t=time.time(), r=r)
    return r


def do_update():
    """Baixa o instalador da versao nova pra pasta temporaria e abre. So aceita link do proprio repositorio."""
    r = check_update()
    url = r.get("url") or ""
    if not r.get("nova") or not url.startswith(f"https://github.com/{REPO}/releases/download/"):
        return {"error": "Não achei o instalador da versão nova."}
    import subprocess
    import tempfile
    import urllib.request
    dest = os.path.join(tempfile.gettempdir(), f"Instalar-Tradutor-de-Call-{r['nova']}.exe")
    req = urllib.request.Request(url, headers={"User-Agent": "tradutor-de-call"})
    with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as f:
        total, done, last = int(resp.headers.get("Content-Length") or 0), 0, 0
        while True:
            chunk = resp.read(1 << 18)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total and done - last > total / 50:
                last = done
                broadcast({"type": "update_progress", "v": round(done / total, 3)})
    subprocess.Popen([dest], close_fds=True)
    threading.Timer(1.5, lambda: os._exit(0)).start()    # fecha o app pra o instalador trocar os arquivos
    return {"ok": True}


def start(block=True):
    """Carrega modelos, liga captura e servidor. block=False devolve o servidor rodando numa thread."""
    global en2pt, pt2en
    state["only_en"] = bool(load_conf().get("so_ingles", True))
    state["ctx"] = load_conf().get("contexto", "crown")
    state["speed"] = load_conf().get("velocidade", "equil")
    from faster_whisper import WhisperModel
    log("carregando modelos (uns segundos)...")
    try:  # placa de video + modelo grande
        if os.environ.get("TRADUTOR_SEM_GPU") == "1":   # PC sem placa NVIDIA: nem tenta (nao baixa o modelo grande a toa)
            raise RuntimeError("sem placa NVIDIA")
        model = WhisperModel(CFG["whisper"], device="cuda", compute_type="int8")
        model.transcribe(np.zeros(SR, dtype=np.float32), language="en")
        log("whisper:", CFG["whisper"], "na placa de video")
    except Exception as e:  # sem GPU: modelo pequeno no processador
        log("GPU falhou, usando CPU:", repr(e)[:150])
        CFG["partial_every"] = 1.2
        # "small" (e nao "small.en"): entende varias linguas, entao o filtro "so ingles" continua valendo
        model = WhisperModel(os.environ.get("TRADUTOR_MODELO_CPU", "small"), device="cpu", compute_type="int8",
                             cpu_threads=max(2, (os.cpu_count() or 4) - 2))
    _mt = MT("ooeoeo/opus-mt-tc-big-en-pt-ct2-float16")
    en2pt = lambda t, beam=2: pt_br(_mt(t, beam))
    pt2en = MT("ooeoeo/opus-mt-ROMANCE-en-ct2-float16")
    q = queue.Queue()
    threading.Thread(target=capture_loop, args=(q,), daemon=True).start()
    threading.Thread(target=main_loop, args=(q, model, en2pt), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    log(f"pronto: http://localhost:{PORT}")
    log("transcricao salva em:", logfile)
    if block:
        srv.serve_forever()
    else:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    start()
