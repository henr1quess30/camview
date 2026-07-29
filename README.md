# CamView

CamView é um cliente desktop nativo para Linux focado em visualização ao
vivo de câmeras/NVRs via RTSP, organizadas em mosaicos — semelhante à parte
de live view do Hikvision iVMS-4200, porém mais leve e sem os demais
recursos (sem gravação local, sem IA, sem detecção de movimento, sem
servidor web).

Testado em EndeavourOS/Arch Linux com KDE Plasma.

## Instalação

### Recomendado: Flatpak (qualquer distribuição)

Traz tudo junto — inclusive o VLC e os codecs. Você não precisa instalar
dependência nenhuma à mão.

1. Baixe o `CamView.flatpak` e o `install-flatpak.sh` da
   [página de releases](https://github.com/henr1quess30/camview/releases).
2. Rode:

```bash
chmod +x install-flatpak.sh
./install-flatpak.sh CamView.flatpak
```

O script instala o runtime, os codecs e o aplicativo, e ao final o CamView
aparece no menu. A primeira execução baixa alguns GB de runtime; as
seguintes são instantâneas.

Se preferir fazer à mão:

```bash
flatpak remote-add --if-not-exists --user flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user flathub org.kde.Platform//6.9 org.freedesktop.Platform.ffmpeg-full//24.08
flatpak install --user --bundle CamView.flatpak
flatpak run io.github.henr1quess30.CamView
```

> **Só falta o `flatpak`?** `sudo pacman -S flatpak` (Arch/EndeavourOS),
> `sudo apt install flatpak` (Ubuntu/Mint/Debian). No Fedora já vem.

#### Atualizar para uma versão nova

Baixe o `CamView.flatpak` da release nova e rode o mesmo script:

```bash
./install-flatpak.sh CamView.flatpak
```

Seus dispositivos, layouts e senhas continuam onde estão — ficam em
`~/.var/app/` e no keyring, que a reinstalação não toca.

> **Por que não `flatpak update`?** Porque um bundle não carrega
> repositório para consultar: o comando responde "Nothing to update" para
> sempre, mesmo com uma release nova publicada. E um
> `flatpak install --bundle` cru recusa com "já instalado". O script cobre
> os dois casos — ele detecta o que já está lá, desinstala e instala a
> versão nova, preservando seus dados.

### AppImage (um arquivo só, sem instalar nada)

Baixe o `CamView-*-x86_64.AppImage` da
[página de releases](https://github.com/henr1quess30/camview/releases) e
rode:

```bash
chmod +x CamView-*-x86_64.AppImage
./CamView-*-x86_64.AppImage
```

> **Erro sobre `libfuse.so.2`?** Ubuntu 22.04+, Mint e Debian 12+ não
> trazem mais essa biblioteca. Rode
> `./CamView-*-x86_64.AppImage --appimage-extract-and-run`, que dispensa
> o FUSE, ou instale-a: `sudo apt install libfuse2` (ou `libfuse2t64` nas
> versões mais recentes).

Construído sobre glibc 2.35, o que cobre Ubuntu 22.04+, Debian 12+,
Fedora 36+ e o Mint atual. Distribuições mais antigas que isso precisam
do Flatpak.

Vale saber que **AppImage e Flatpak não compartilham dados**: o primeiro
guarda em `~/.local/share/camview/` e o segundo em `~/.var/app/`. Instalar
os dois na mesma máquina dá duas listas de câmeras independentes.

### Arch/EndeavourOS pelo PKGBUILD

```bash
git clone https://github.com/henr1quess30/camview.git
cd camview/packaging && makepkg -si
```

### A partir do código (para desenvolver)

```bash
sudo pacman -S python python-pip vlc     # ou o equivalente na sua distro
git clone https://github.com/henr1quess30/camview.git
cd camview
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m camview
```

**Atenção nesta rota:** instale o pacote `vlc` completo, não apenas o
`libvlc`. No Arch o `libvlc` sozinho **não reproduz RTSP** — o transporte
(`vlc-plugin-live555`), os decodificadores (`vlc-plugin-ffmpeg`) e as
saídas de vídeo (`vlc-plugins-video-output`) são pacotes separados.
Instalar `vlc` resolve os três de uma vez. O Flatpak acima não tem esse
problema porque já embute tudo.

## Construir o próprio Flatpak

```bash
cd packaging/flatpak && ./build.sh            # instala para o seu usuário
cd packaging/flatpak && ./build.sh --bundle   # e gera CamView.flatpak
```

Veja [packaging/flatpak/README.md](packaging/flatpak/README.md) — explica
por que o manifesto compila o VLC e como publicar no Flathub.

### Atalho no menu do KDE

Para abrir o CamView pelo menu de aplicativos, sem terminal:

```bash
./scripts/install-desktop-entry.sh
```

O script aponta o atalho para o Python do virtualenv deste checkout, então
não é preciso ativar o ambiente antes de abrir.

## Quando algo dá errado

O CamView foi feito para não morrer por causa de uma falha: erro de banco,
keyring indisponível, NVR fora do ar ou libVLC ausente viram mensagem na
tela e registro no log, nunca um fechamento inesperado.

- **Logs:** `~/.local/state/camview/logs/camview.log` (o diretório é
  configurável em Configurações → Logs).
- **Uma célula em vermelho** mostra o motivo e reconecta sozinha, com
  intervalo crescente. Depois de várias falhas seguidas ela passa a
  sugerir conferir usuário e senha.
- **Canal sem câmera:** depois de duas falhas, o CamView pergunta ao
  próprio NVR se aquele canal está transmitindo. Se o equipamento
  responde que não, a célula para de insistir a cada poucos segundos,
  informa "sem transmissão" e volta a tentar de 5 em 5 minutos — assim
  ela reaparece sozinha quando a câmera for consertada, sem ficar
  martelando o gravador enquanto isso.
- **Cuidado com senha errada:** NVRs Hikvision bloqueiam o IP de origem
  após algumas tentativas de login malsucedidas (o bloqueio costuma cair
  sozinho em ~30 min). Por isso o CamView se recusa a abrir streams de um
  NVR sem senha armazenada em vez de tentar e falhar.
- **Banco corrompido:** o app explica o problema na abertura e indica
  renomear `~/.local/share/camview/camview.db`; um banco novo é criado na
  próxima execução. As senhas continuam no keyring.

## Desenvolvimento

Rodar os testes:

```bash
pytest                                    # 454 testes, roda sem display
pytest --cov=camview --cov-report=term-missing   # cobertura
```

A suíte nunca abre janela nem acessa a rede: usa `QT_QPA_PLATFORM=offscreen`
automaticamente, um libVLC falso, um keyring em memória e endereços da
faixa de documentação (`192.0.2.x`). Nenhuma senha real em fixtures.

### Estrutura do projeto

```
src/camview/
├── app.py              # bootstrap do QApplication, logging, excepthook
├── config.py            # paths XDG (banco de dados, logs)
├── logging_setup.py      # configuração de logging
├── database/             # conexão SQLite, migrações, repositórios
├── models/               # dataclasses/enums (Nvr, Camera, Layout, ...)
├── services/              # geração de URL RTSP, keyring, gerenciamento do VLC
└── ui/                    # janela principal, diálogos, widgets
```

### Padrões de código

- Type hints em todo o código.
- `logging` em vez de `print`.
- `pathlib` em vez de manipulação manual de strings de caminho.
- Nenhuma operação demora na thread da GUI (Qt main thread).
- Uma única instância global de `vlc.Instance`, encapsulada em
  `services/stream_manager.py` — a única exceção deliberada à regra de
  evitar variáveis globais.

## Status do projeto

Em desenvolvimento incremental. Veja [PLAN.md](PLAN.md) para o roteiro
completo de fases e o que já foi concluído.

**Versão 0.2.0** — as 12 fases do [PLAN.md](PLAN.md) estão concluídas, e o
desenvolvimento segue com melhorias pedidas em uso real. Validado contra
dez NVRs Hikvision, com até 16 streams simultâneos. Veja o
[CHANGELOG.md](CHANGELOG.md).

A interface segue o tema do sistema (ícones e cores do KDE Plasma,
inclusive no modo escuro) — não há tema próprio embutido.

Como usar hoje:

- Menu **Dispositivos → Gerenciar dispositivos...** (`Ctrl+D`, ou o botão
  **Dispositivos** na barra) abre a tabela com tudo que está cadastrado:
  nome, endereço, tipo, canais e se a senha está guardada. Marque as
  caixas para **excluir vários de uma vez** — uma única confirmação, que
  lista o que vai embora — ou para atualizar canais em lote. O campo de
  filtro encurta a lista por nome ou endereço. Adicionar, editar e
  excluir começam todos por aqui.
- Menu **Dispositivos → Adicionar NVR...** para cadastrar um equipamento. No campo
  **Dispositivo** escolha **NVR/DVR** (os canais são gerados
  automaticamente) ou **Câmera avulsa** — nesse caso não há canais a
  informar, ela aparece como uma linha única na lista e o duplo clique
  **abre direto em tela cheia**.
- Menu **Dispositivos → Adicionar câmeras por URL...** para cadastrar em lote: cole
  uma lista de URLs RTSP (uma por linha) e cada uma vira uma câmera. É o
  caminho para equipamentos que não seguem a numeração de canais da
  Hikvision — o caminho do stream vem da própria URL, como
  `rtsp://usuario:senha@192.168.0.10:554/live/main`. As senhas vão para o
  keyring; a URL não é guardada.
- **Duplo clique no NVR** abre **todos os canais dele de uma vez**, ajustando
  a grade automaticamente ao número de câmeras (4 canais → 2x2, 16 → 4x4).
- **Botão direito num dispositivo → "Atualizar canais e nomes"** pergunta ao
  equipamento quais canais existem e como as câmeras se chamam, criando o
  que falta e adotando os nomes de lá. Nomes genéricos ("Canal 7") são
  substituídos pelos reais; um nome de verdade nunca é rebaixado a
  genérico.
- **Duplo clique** numa câmera da árvore lateral abre o stream na primeira
  célula livre; ou **arraste** a câmera para uma célula específica.
- **Arraste** uma célula sobre outra para trocá-las de posição.
- **Duplo clique** numa célula maximiza; **duplo clique** de novo ou **Esc**
  volta ao mosaico. Ao maximizar, a câmera troca automaticamente para o
  stream principal (mais nitidez na tela cheia) e volta ao substream ao
  restaurar.
- O seletor na barra superior alterna entre os mosaicos **1x1, 2x2, 3x3,
  4x4** e os de célula destacada **1+5, 1+7 e 1+12** — uma câmera grande
  com as demais em volta, que é como se costuma acompanhar um local:
  uma vista que importa e as outras no canto do olho.
- O cabeçalho de cada célula mostra **SUB** ou **PRINCIPAL**, indicando qual
  stream está em uso, e a barra inferior mostra quantas células estão
  ocupadas.
- No topo da lista lateral, um **painel de status** com relógio, uso de CPU
  e memória do CamView, quantas câmeras estão realmente reproduzindo e
  quanta banda os streams estão consumindo. Pode ser desligado em
  Configurações → Ao abrir.
- **Roda do mouse** sobre uma câmera dá zoom digital, aproximando o ponto
  sob o cursor (até 8x). Com a imagem ampliada, **arraste com o botão
  esquerdo para mover a imagem**; `0` volta ao normal (e aí arrastar
  volta a mover a célula de lugar).
- Com uma câmera em tela cheia, **→ e ←** passam para a próxima e a
  anterior sem precisar voltar ao mosaico.

### Zoom e navegação pelo teclado

| Atalho | O que faz |
|--------|-----------|
| `→` / `←` | Próxima / câmera anterior (em tela cheia ou na seleção) |
| `+` / `-` | Aproximar / afastar |
| `0` | Voltar ao enquadramento normal |
| `Esc` | Sair da tela cheia |

Todos configuráveis em **Configurações → Atalhos de teclado**.

Ao passar de câmera em câmera, a imagem aparece **na hora**: a célula já
está reproduzindo desde o mosaico. Se você parar numa câmera por mais de
um segundo, ela sobe sozinha para o stream principal — passar direto não
reinicia nada.

Mosaicos usam substream automaticamente (menos banda e CPU); a grade 1x1
respeita o stream padrão configurado no NVR.

Se uma célula travar — o NVR às vezes para de enviar sem avisar, e nesse
caso o libVLC não reporta erro nenhum — o CamView percebe em ~10 segundos
(nenhum quadro novo na tela) e reconecta aquela célula sozinho, sem mexer
nas outras.

### Mosaico picotado? Troque o stream

O mosaico usa substream para economizar banda e CPU, e muitos NVRs
entregam o substream com taxa de quadros baixa (10 fps contra 25 do
principal, em dois equipamentos testados aqui). Duas saídas:

- **Botão direito numa célula** → "Stream principal". Vale só para aquela
  célula, sobrevive a maximizar/restaurar e é gravada no layout.
- **Configurações → Stream do mosaico**, se você preferir mudar todas de
  uma vez (ou seguir o padrão configurado em cada NVR).

Medido num NVR real: 13 fps a 640x360 no substream contra 29,8 fps a
1920x1080 no principal, na mesma célula.

## Configurações

Menu **File → Configurações...** (`Ctrl+,`):

- **Reprodução:** latência (buffer), transporte RTSP (TCP ou UDP), stream
  do mosaico e áudio.
- **Reconexão:** ligar/desligar a reconexão automática e o intervalo
  máximo entre tentativas.
- **Ao abrir:** iniciar maximizado e reabrir o último layout.
- **Logs:** diretório dos arquivos de log (passa a valer na próxima
  abertura).

As opções de reprodução chegam ao libVLC quando um stream começa, então
valem para as células abertas ou reconectadas dali em diante — as que já
estão rodando continuam como estavam.

### Layouts salvos

Monte o mosaico como quiser e guarde-o com um nome ("Fábrica", "Portaria"):

- **Layouts → Novo layout** (`Ctrl+N`) — fecha todas as câmeras e deixa o
  mosaico em branco para você montar do zero (pergunta antes, se houver
  câmeras abertas). A grade escolhida é mantida.
- **Layouts → Salvar como...** (`Ctrl+Shift+S`) — pede o nome e grava a
  grade, quais câmeras estão em cada célula e o stream de cada uma.
- **Layouts → Salvar** (`Ctrl+S`) — sobrescreve o layout que está aberto
  (o nome aparece no título da janela); se nenhum estiver aberto, pede um.
- **Layouts → *nome do layout*** — carrega em um clique, ajustando a grade.
- **Layouts → Gerenciar layouts...** — carregar, renomear e excluir.

Câmeras removidas depois que o layout foi salvo simplesmente não abrem — o
resto do layout carrega normalmente e a status bar informa quantas ficaram
de fora.

Ao fechar o CamView, ele guarda o tamanho/posição da janela, a grade e o
layout aberto; na próxima vez tudo volta como estava — inclusive as
câmeras reproduzindo. Mosaico montado na hora e não salvo não é
restaurado: só layouts nomeados voltam.

### Nota sobre Wayland

O libVLC 3.x só sabe embutir vídeo em janela X11. Em sessão Wayland
nativa isso falha, então o CamView força `QT_QPA_PLATFORM=xcb` no
startup e roda via XWayland. Isso é automático — nada a configurar.

## Licença

MIT — veja [LICENSE](LICENSE).
