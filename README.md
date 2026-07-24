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

Fase atual: **Fase 3 concluída** — reprodução RTSP real funcionando
(validada contra um NVR Hikvision com stream H.265 1080p), com uma célula
de vídeo por vez: duplo clique numa câmera da árvore abre o stream, com
indicador de status, mensagem de erro e reconexão automática com backoff.
O mosaico com várias células vem na Fase 4.

### Nota sobre Wayland

O libVLC 3.x só sabe embutir vídeo em janela X11. Em sessão Wayland
nativa isso falha, então o CamView força `QT_QPA_PLATFORM=xcb` no
startup e roda via XWayland. Isso é automático — nada a configurar.

## Licença

MIT — veja [LICENSE](LICENSE).
