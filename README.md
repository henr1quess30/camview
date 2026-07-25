# CamView

CamView é um cliente desktop nativo para Linux focado em visualização ao
vivo de câmeras/NVRs via RTSP, organizadas em mosaicos — semelhante à parte
de live view do Hikvision iVMS-4200, porém mais leve e sem os demais
recursos (sem gravação local, sem IA, sem detecção de movimento, sem
servidor web).

Testado em EndeavourOS/Arch Linux com KDE Plasma.

## Requisitos de sistema (EndeavourOS / Arch Linux)

```bash
sudo pacman -S python python-pip vlc
```

Notas sobre essas dependências, confirmadas em ambiente real:

- **Instale o pacote `vlc` completo, não apenas `libvlc`.** No Arch, o
  `libvlc` sozinho **não reproduz RTSP** — os plugins essenciais são
  pacotes separados. Se você só tem `libvlc`, precisa no mínimo de:

  ```bash
  sudo pacman -S vlc-plugin-live555 vlc-plugin-ffmpeg vlc-plugins-video-output
  ```

  - `vlc-plugin-live555` — transporte RTSP. Sem ele o VLC tenta os
    módulos `satip`/`realrtsp` e a conexão falha sempre.
  - `vlc-plugin-ffmpeg` — decodificação H.264/H.265.
  - `vlc-plugins-video-output` — os módulos de saída de vídeo. Sem ele
    não há nenhuma saída disponível.

  Instalar `vlc` resolve tudo de uma vez e é o caminho recomendado.
- O pacote `python` do Arch **não inclui pip/ensurepip**. `python -m venv`
  funciona sem `python-pip`, mas o `pip` dentro do venv não bootstrap
  sozinho — por isso `python-pip` precisa estar instalado no sistema antes
  do passo `pip install -e ".[dev]"` abaixo, mesmo que a instalação em si
  aconteça dentro do ambiente virtual.

## Instalação e execução

```bash
git clone <repo> camview
cd camview
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m camview
```

### Atalho no menu do KDE

Para abrir o CamView pelo menu de aplicativos, sem terminal:

```bash
./scripts/install-desktop-entry.sh
```

O script aponta o atalho para o Python do virtualenv deste checkout, então
não é preciso ativar o ambiente antes de abrir.

## Desenvolvimento

Rodar os testes:

```bash
pytest
```

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

Fase atual: **Fase 7 concluída** — mosaico, layouts salvos, restauração da
sessão e tela de configurações, validados contra NVRs Hikvision reais com
até 16 streams simultâneos.

Como usar hoje:

- Menu **NVR → Adicionar NVR...** para cadastrar um equipamento (os canais
  são gerados automaticamente).
- **Duplo clique no NVR** abre **todos os canais dele de uma vez**, ajustando
  a grade automaticamente ao número de câmeras (4 canais → 2x2, 16 → 4x4).
- **Duplo clique** numa câmera da árvore lateral abre o stream na primeira
  célula livre; ou **arraste** a câmera para uma célula específica.
- **Arraste** uma célula sobre outra para trocá-las de posição.
- **Duplo clique** numa célula maximiza; **duplo clique** de novo ou **Esc**
  volta ao mosaico. Ao maximizar, a câmera troca automaticamente para o
  stream principal (mais nitidez na tela cheia) e volta ao substream ao
  restaurar.
- O seletor na barra superior alterna entre **1x1, 2x2, 3x3 e 4x4**.

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
