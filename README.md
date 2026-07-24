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

- O pacote `vlc` traz consigo a `libvlc` e os codecs comuns — é o jeito
  recomendado de garantir suporte a H.264/H.265. Se `libvlc` já estiver
  instalada isoladamente (por outra dependência), o `python-vlc` já
  funciona, mas instalar `vlc` continua sendo a forma mais confiável de
  obter os codecs junto.
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

Fase atual: **Fase 1 concluída** — persistência SQLite (schema, migrações,
repositórios com CRUD) além da estrutura do projeto e janela principal
funcional. Ainda sem cadastro de NVR, streams ou mosaico.

## Licença

MIT — veja [LICENSE](LICENSE).
