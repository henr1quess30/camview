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
- [x] Fase 3 — Célula de vídeo única
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

## Fase 3 — Célula de vídeo única ✅

Instância global única de `vlc.Instance` (lazy, construída sob demanda) em
`services/stream_manager.py`. `VideoTile` com um `vlc.MediaPlayer` próprio,
indicador de status de conexão, mensagem de falha, botão de fechar.
Reconexão com backoff (2s → 5s → 10s → máx. 30s) via `ReconnectBackoff`
(`services/reconnect.py`), agendada por `QTimer` — a conexão em si é
assíncrona dentro do libVLC, então nada bloqueia a thread da GUI. Opções
de baixa latência por stream (`network-caching`, `rtsp-tcp`, `no-audio`)
em `PlaybackOptions`.

### Resolução do risco Wayland/X11

Confirmado empiricamente: em sessão **Wayland nativa** o libVLC 3.0.x
falha completamente (`video output creation failed`), pois embute vídeo
por handle X11 e `winId()` do Qt devolve um `wl_surface`. **Solução
adotada:** `os.environ.setdefault("QT_QPA_PLATFORM", "xcb")` em
`__main__.py`, antes de qualquer import do PySide6 — o app inteiro roda
via XWayland e o embedding funciona. Não foi necessário fallback por
callback de vídeo/vmem.

### Dependências de sistema descobertas (críticas)

O pacote `libvlc` do Arch **sozinho não reproduz RTSP**. Faltavam três
pacotes, todos descobertos ao testar contra um NVR real:

- `vlc-plugin-live555` — transporte RTSP. Sem ele o libVLC cai nos
  módulos `satip`/`realrtsp` e falha sempre.
- `vlc-plugin-ffmpeg` — decodificação H.264/H.265.
- `vlc-plugins-video-output` — os módulos de saída de vídeo (`xcb_x11`
  etc.). Sem ele **nenhuma** saída existe.

Instalar o pacote `vlc` traz todos. Isso invalidou um spike inicial meu:
como nenhum módulo de vout existia, todos os valores de `--vout` que
testei falhavam de forma idêntica, o que me levou a escolher um nome de
módulo inexistente (`x11`). O correto é `xcb_x11`.

### Bug de design encontrado e corrigido

O `QStackedLayout` do `VideoTile` escondia o widget de vídeo enquanto
exibia "Conectando...". Um widget X11 não-mapeado não pode receber saída
de vídeo, então a criação do vout falhava. Corrigido com
`StackingMode.StackAll`, que mantém todas as páginas mapeadas.

### Validação real

Testado contra dois NVRs Hikvision reais:

- **Célula isolada:** stream H.265 1080p conectado, decodificado e
  renderizado. Comprovado via `video_take_snapshot()` do próprio libVLC
  — frame de 1920x1080 com ~44 mil cores únicas (imagem real).
- **Fluxo completo pelo `MainWindow`:** duplo clique numa câmera da
  árvore abriu o stream correto (`Canal 1`), chegou a `PLAYING` com
  vídeo 720p na primeira tentativa, **zero** falhas de autenticação, e
  o player foi corretamente liberado ao fechar a célula.

**Ruído benigno conhecido:** o libVLC 3.x sonda caminhos de hardware na
inicialização e loga `glconv_vaapi_x11 gl error: vaDeriveImage` /
`video output creation failed` antes de cair para software. A
reprodução funciona apesar dessas mensagens. Roteá-las para o `logging`
do Python (via `libvlc_log_set`) fica como melhoria futura — exige lidar
com `va_list` por ctypes.

**Lição aprendida (proteção adicionada):** durante os testes, um bug no
*script de teste* (monkeypatch da senha no módulo errado) gerou
tentativas com senha vazia, e o NVR bloqueou o IP de origem — comportamento
padrão dos Hikvision após algumas falhas de login. Para evitar que isso
aconteça com usuários reais, `_show_camera_stream()` agora recusa abrir
o stream quando não há senha armazenada, exibindo uma mensagem clara em
vez de tentar autenticar. Coberto por teste de regressão.

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
