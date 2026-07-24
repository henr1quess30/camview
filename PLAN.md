# CamView — Plano de Desenvolvimento

Cliente desktop nativo para Linux (foco: EndeavourOS/Arch + KDE Plasma) para
visualização ao vivo de câmeras/NVRs via RTSP, organizadas em mosaicos.
Sem gravação local, IA, detecção de movimento ou servidor web — apenas live view.

Stack: Python 3.12+, PySide6, `python-vlc` (libVLC), SQLite, `keyring`, `pytest`.

O desenvolvimento é incremental. Cada fase deve ser concluída, testada e
validada antes de avançar para a próxima.

## Status

- [x] Fase 0 — Scaffold & janela vazia
- [x] Fase 1 — Persistência SQLite
- [x] Fase 2 — Cadastro de NVR e geração de canais Hikvision
- [ ] Fase 3 — Célula de vídeo única
- [ ] Fase 4 — Mosaico
- [ ] Fase 5 — Layouts salvos
- [ ] Fase 6 — Restauração de estado ao abrir
- [ ] Fase 7 — Tela de configurações
- [ ] Fase 8 — Tratamento de erros
- [ ] Fase 9 — Polimento de UI
- [ ] Fase 10 — Testes
- [ ] Fase 11 — Empacotamento e documentação final

## Fase 0 — Scaffold & janela vazia

Estrutura de pastas (`src/camview/...`), `pyproject.toml`, `MainWindow`
funcional (sidebar, área central placeholder, toolbar, status bar),
`README.md` com instruções para Arch/EndeavourOS, teste de smoke,
repositório git inicializado.

## Fase 1 — Persistência SQLite ✅

Schema completo via migração versionada (`PRAGMA user_version`):
`nvrs`, `cameras`, `layouts`, `layout_items`, `settings`. Repositórios
(`NvrRepository`, `CameraRepository`, `LayoutRepository`, `SettingsRepository`)
com CRUD. Testes contra banco temporário (sem tocar no banco real do usuário).

Concluída também nesta fase (adiantado em relação ao rascunho original, pois
os repositórios já precisavam de tipos concretos): dataclasses `Nvr`,
`Camera`, `Layout`, `LayoutItem` e o enum `StreamType`. `initialize_database()`
é chamado no startup real do app (`__main__.py`), criando o banco em
`~/.local/share/camview/camview.db` automaticamente no primeiro uso —
validado rodando o app de verdade.

## Fase 2 — Cadastro de NVR e geração de canais Hikvision ✅

`services/credentials.py` como wrapper do `keyring` (senha nunca em texto puro
no SQLite; falha do keyring vira `CredentialsError`, tratada na UI).
`services/rtsp.py::build_channel_url()` — função pura e testável que gera
URLs no padrão Hikvision (`101`, `102`, `201`, `202`, ...) a partir de canal +
tipo de stream, com escape de caracteres especiais em usuário/senha; nunca
persiste a senha em disco. `generate_missing_channel_cameras()` gera os
registros de câmera para os canais que ainda não existem (usado tanto no
cadastro inicial quanto ao aumentar `channel_count` numa edição, sem apagar
canais existentes). `services/connectivity.py::check_tcp_connection()` —
teste de alcançabilidade TCP (sem negociação RTSP real, que é Fase 3).

`NvrDialog` (nome, host, porta, usuário, senha, quantidade de canais, stream
padrão) com botão "Testar conexão" rodando em `QThread` para não bloquear a
UI. `DeviceTree` populada a partir do banco (NVR → câmeras), com menu de
contexto para editar/remover cada NVR, integrado ao `MainWindow` (menu NVR →
Adicionar).

Bug real encontrado pelos testes de integração e corrigido: o `QComboBox` de
stream padrão perdia o tipo `StreamType` ao ler `currentData()` (o Qt
devolvia a `str` "crua" por `StreamType` herdar de `str`) — corrigido
reconstruindo o enum explicitamente em `NvrDialog.result_nvr()`.

## Fase 3 — Célula de vídeo única

Instância global única de `vlc.Instance` (lazy, construída sob demanda) em
`services/stream_manager.py`. `VideoTile` com um `vlc.MediaPlayer` próprio,
indicador de status de conexão, mensagem de falha, botão de fechar.
Conexão/reconexão rodando fora da thread da GUI (worker dedicado), com
backoff exponencial (2s → 5s → 10s → máx. 30s). Opções de baixa latência
configuráveis (`--network-caching`, `--rtsp-tcp`, `--no-audio`).

**Risco conhecido:** libVLC 3.0.x embute vídeo via handle X11
(`libvlc_media_player_set_xwindow`). Em sessão Wayland pura, `widget.winId()`
do Qt não é um X11 window id — só funciona hoje via XWayland. Esta fase
precisa de um spike para validar o embedding real nesta máquina e documentar
um fallback (ex.: callback de vídeo/vmem) caso necessário.

## Fase 4 — Mosaico

`VideoGrid` suportando até 16 células (`QGridLayout`), grades 1x1/2x2/3x3/4x4
selecionáveis pela toolbar. Adicionar câmera por duplo clique (primeira célula
livre) ou drag-and-drop numa célula específica; trocar câmeras de posição por
drag-and-drop. Maximizar célula com duplo clique / restaurar com duplo clique
ou Esc.

## Fase 5 — Layouts salvos

CRUD de layouts (criar, renomear, sobrescrever, excluir, carregar),
persistindo grade + câmera/stream por posição nas tabelas `layouts` e
`layout_items`.

## Fase 6 — Restauração de estado ao abrir

Ao iniciar, restaurar: último layout usado, geometria/posição da janela,
estado maximizado, última grade selecionada — via tabela `settings` e
`closeEvent` da janela principal.

## Fase 7 — Tela de configurações

Latência/network caching, transporte RTSP (TCP/UDP), reconexão automática
e tempo máximo de backoff, iniciar maximizado, abrir último layout, mudo,
stream padrão (principal/substream), diretório de logs — tudo persistido
na tabela `settings` e aplicado a players novos/reconectando.

## Fase 8 — Tratamento de erros

Auditoria de todos os cenários de falha: IP/porta inválidos, credenciais
incorretas, NVR inacessível, timeout, stream indisponível, codec não
suportado, VLC não instalado, keyring indisponível, banco corrompido.
Cada caso deve ser capturado na camada correta, logado com detalhe técnico
e apresentado ao usuário com mensagem compreensível — nenhuma exceção não
tratada pode encerrar o aplicativo.

## Fase 9 — Polimento de UI

Tema escuro compatível com KDE Plasma, ícones do tema do sistema, espaçamento
consistente, borda de seleção discreta na câmera focada, revisão visual de
todos os diálogos e da toolbar.

## Fase 10 — Testes

Cobertura de: repositórios de banco, geração de URL RTSP, round-trip de
layouts (salvar/carregar), resolução de paths de configuração. Módulos
puros com cobertura real; código dependente de UI/VLC com mocks/thin seams.
Nenhuma senha real em fixtures de teste.

## Fase 11 — Empacotamento e documentação final

Validar `pip install -e ".[dev]"` fim a fim, preparar a estrutura para
AppImage e PKGBUILD do Arch (estrutural, não construído ainda), finalizar
`README.md`, adicionar `CHANGELOG.md`.
