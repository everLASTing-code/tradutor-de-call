# Tradutor de call

Programa de Windows que ouve o que sai no teu fone (Discord, Meet, Zoom…), transcreve o inglês em tempo real e mostra a tradução em português. Também te ajuda a responder: sugere respostas prontas em inglês, traduz o que você escreve em português e anota os tópicos da call.

A transcrição e a tradução rodam no teu PC. Nada do áudio sai da máquina.

## Baixar

1. Abre a página de [versões](../../releases/latest) e baixa o `Instalar-Tradutor-de-Call.exe`.
2. Roda o instalador. Ele não pede administrador e cria o atalho "Tradutor de call" na Área de Trabalho.
3. Na primeira abertura o programa baixa os modelos (uns 2 GB). Em PC com placa NVIDIA ele baixa também os arquivos da placa (mais 1,3 GB). Isso acontece uma vez só.

O Windows pode mostrar o aviso "O Windows protegeu o computador" porque o instalador não tem assinatura paga. Clica em "Mais informações" e depois em "Executar assim mesmo".

## O que precisa

- Windows 10 (versão 2004 ou mais nova) ou Windows 11.
- Internet na primeira abertura.
- Placa NVIDIA é opcional. Com ela a transcrição sai em cerca de 1 segundo e usa o modelo grande. Sem ela o programa funciona com um modelo menor, mais lento e que erra mais.

## Como usar

- A barra flutua por cima das outras janelas. Arrasta pelos pontinhos.
- **Bolinha da esquerda:** liga e desliga a captação. O programa abre desligado.
- **Ouvir:** mostra a call traduzida. Por padrão ele só age quando ouve inglês e ignora outras línguas, tosse e risada.
- **Responder:** escreve em português e aperta Enter pra virar inglês, ou clica em "Sugerir resposta" pra receber três respostas prontas pra última fala.
- **Notas:** bloco de notas que salva sozinho. Com uma IA ligada, ele anota os tópicos da call.
- **Ajustes:** tipo de call, velocidade, cores, tema, qual IA usar e os teus textos de contexto.

### Atalhos (funcionam com outra janela na frente)

| Atalho | O que faz |
|---|---|
| Ctrl+Shift+L | liga ou desliga a captação |
| Ctrl+Shift+R | sugere respostas |
| Ctrl+Shift+1 / 2 / 3 | copia uma das respostas |
| Ctrl+Shift+H | esconde ou mostra tudo |
| Ctrl+Shift+P | aparece ou some em print e tela compartilhada |
| Ctrl+Shift+↑ / ↓ | transparência |

## Respostas sugeridas e anotações (opcional)

Essas duas funções usam uma IA de fora, e só quando você pede. Em Ajustes → Respostas escolhe Gemini, ChatGPT, Claude ou Grok e cola a tua chave de API. A chave fica só no teu PC. Com "Desligado" o programa nunca chama nenhuma API e o resto funciona igual.

No mesmo lugar dá pra escrever o teu **contexto fixo** (quem você é, com o que trabalha) e os **nomes e termos** do teu trabalho. O programa já vem com um contexto do nicho de trading cards e anúncios; troca pelo teu se for outro assunto.

## Onde ficam as tuas coisas

Em `%LOCALAPPDATA%\TradutorDeCall`: configurações e chaves (`config.json`), o bloco de notas, as transcrições de cada call e o registro do programa. Atualizar ou reinstalar não apaga essa pasta.

## Atualização

Ao abrir, o programa confere se saiu versão nova aqui no GitHub. Se saiu, aparece um botão "Atualizar" embaixo da barra, que baixa e roda o instalador novo.

## Rodar pelo código

```powershell
pip install -r requirements.txt
pythonw "Tradutor de call.pyw"
```

Pra usar a placa NVIDIA rodando pelo código: `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`.

Pra gerar o instalador: `powershell -ExecutionPolicy Bypass -File gerar_exe.ps1` (precisa do PyInstaller).

## Como funciona por dentro

- Áudio do sistema por WASAPI loopback (`soundcard`).
- Transcrição com `faster-whisper` (large-v3-turbo na placa, small no processador).
- Tradução offline com modelos opus-mt em `ctranslate2`.
- Janela em `pywebview` (WebView2), recortada no formato das peças, com a tela em HTML e GSAP.
