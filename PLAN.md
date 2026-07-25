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
- [x] Fase 4 — Mosaico
- [x] Fase 5 — Layouts salvos
- [x] Fase 6 — Restauração de estado ao abrir
- [x] Fase 7 — Tela de configurações
- [x] Fase 8 — Tratamento de erros
- [x] Fase 9 — Polimento de UI
- [x] Fase 10 — Testes
- [x] Fase 11 — Empacotamento e documentação final

## Depois da 0.1.0 — câmera avulsa e canais mortos (pedido do usuário)

Observação do usuário: "fizemos pensando em NVR né — se eu adicionar uma
câmera sozinha ela não fica em tela cheia".

- **`DeviceType` (`nvr` | `camera`)** na tabela `nvrs`, via migração 2
  (`ALTER TABLE ... DEFAULT 'nvr'`, então o que já estava cadastrado
  continua sendo NVR). O nome da classe segue `Nvr` por ser a tabela
  histórica; o campo é que diz o que a linha representa.
- Câmera avulsa: sem quantidade de canais no cadastro, **uma linha só** na
  árvore lateral (pasta com um único filho é ruído) e duplo clique abrindo
  em **1x1**. Canal de NVR continua indo para a próxima célula livre —
  senão montar mosaico clicando em várias câmeras deixaria de funcionar.

### Canais sem transmissão (segunda parte do pedido)

`channel_online_status()` pergunta ao gravador, via
`/ISAPI/ContentMgmt/InputProxy/channels/status`, quais canais têm câmera
online. Depois de 2 falhas seguidas a célula dispara essa consulta (uma
por dispositivo a cada 2 min, em `QThread`); se o equipamento diz que o
canal está offline — ou nem o lista — a célula é **estacionada**: explica
o motivo e passa a tentar a cada 5 minutos, em vez de a cada 30 segundos.

Medido nos equipamentos do usuário: `NVR A` canal 12, `NVR H` canal 14,
`NVR D` canal 1, `NVR G` canal 1 offline; `NVR C` lista 11 canais
contra 16 cadastrados.

**Dois bugs encontrados só porque a validação foi feita no equipamento
real, ambos invisíveis nos testes que eu tinha:**

1. `Signal(int, dict)` marshalla o dicionário como `QVariantMap`, que só
   aceita chaves de texto — como as chaves são números de canal, o mapa
   chegava **vazio** do outro lado da thread (`_pythonToCppCopy: Cannot
   copy-convert (dict) to C++`). Declarado como `Signal(int, object)`.
2. Estacionar a célula não bastava: o erro da tentativa que já estava em
   voo chegava logo depois e restaurava o backoff curto. Daí o estado
   explícito `_parked`, limpo só quando uma nova tentativa começa.

Validado ao vivo no `NVR A`: 15 células reproduzindo, 1 estacionada com
"O NVR informa que este canal está sem transmissão", 0 em loop de retry.

## Depois da 0.1.0 — zoom, navegação e atalhos (pedido do usuário)

- **Zoom digital por recorte.** `VideoTile.zoom_by()` calcula uma região
  da imagem e a aplica com `video_set_crop_geometry`. Recortar em vez de
  escalar mantém a célula preenchida em qualquer zoom — é o que um zoom
  digital deve parecer. O zoom acompanha o cursor (sem isso, cada passo
  volta ao centro do quadro) e é reaplicado em `_on_playing`, porque
  mídia nova começa sem recorte.
- **Navegação entre câmeras** (`VideoGrid.step`), pulando células vazias e
  dando a volta. Decisão de desempenho tomada durante a validação real:
  ao passar de câmera, a célula é maximizada **sem** trocar o stream, e a
  subida para o principal fica agendada para 1,2s depois
  (`STREAM_UPGRADE_DELAY_MS`). Antes disso cada passo trocava o stream, e
  trocar reinicia a reprodução — medido no equipamento: ~3 segundos de
  tela preta por câmera, o que tornava a navegação inútil. Duplo clique
  continua subindo na hora, porque aí a escolha foi deliberada.
- **Atalhos configuráveis** (`AppSettings.shortcut_*` + `QKeySequenceEdit`
  no diálogo). Um atalho vazio ou inválido é ignorado com aviso no log em
  vez de derrubar a construção da janela.
- **Vazamento corrigido:** `_connect()` criava uma mídia por reconexão sem
  liberar a referência própria. O player retém a sua em `set_media`, então
  a nossa tem de ser solta.

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

### Watchdog de travamento (bug encontrado em uso real)

Sintoma relatado: no mosaico, **algumas células congelam enquanto outras
seguem normais**, e a célula só volta ao ser maximizada.

Diagnóstico por medição, usando os contadores do próprio libVLC
(`libvlc_media_get_stats`) por célula — bytes lidos, quadros decodificados
e quadros **exibidos**:

- Um mosaico 3x3 no NVR `192.0.2.3` ficou 3 minutos a 25–30 fps por
  célula, zero quadros perdidos. Não é problema geral de rede/decoder.
- Num 4x4 no `192.0.2.4` uma célula ficou em **0,0 fps por mais de dois
  minutos** enquanto o app continuava exibindo status "playing".
- Não é saturação de CPU: 16 substreams H.265 consomem ~141% de 800%
  disponíveis (i7-860, 8 threads). A GPU (Radeon HD 5000) não decodifica
  H.265, então tudo roda por software mesmo — e cabe.

**Causa:** quando o NVR simplesmente para de enviar, o libVLC **não emite
`EncounteredError` nem `EndReached`** — o player continua "tocando" e a
imagem congela. Sem evento, nada disparava a reconexão que já existia
desde a Fase 3. Maximizar "resolvia" porque troca de stream e reconecta.

**Correção:** `VideoTile` passou a ter um watchdog (`_check_for_stall`,
a cada 2s) que compara `displayed_pictures` ao longo do tempo; 10s sem
nenhum quadro novo contam como travamento e disparam a mesma reconexão
com backoff. Detalhes que evitam efeitos colaterais:

- **Contador desconhecido nunca é travamento.** Se o libVLC não devolve
  as estatísticas, o watchdog não faz nada — caso contrário uma versão
  sem esse campo colocaria todas as células em loop de reconexão.
- **Célula oculta não é julgada.** Com outra célula maximizada as demais
  ficam escondidas e podem legitimamente parar de renderizar.
- **Backoff só zera após 30s de reprodução contínua**
  (`HEALTHY_PLAYBACK_S`). Antes, o backoff zerava assim que o player
  chegava a "playing", então um stream que trava a cada poucos segundos
  reconectaria eternamente no menor intervalo.
- **Limite de 10s** é generoso de propósito: há substreams configurados a
  10 fps (`192.0.2.6`, `198.51.100.206`) e equipamentos permitem até
  1 fps — lento não pode ser confundido com morto.

Validado ao vivo: congelando artificialmente o contador de uma célula,
o app detectou em 10s, reconectou e voltou a reproduzir, sem perturbar as
outras três; e um mosaico real de 16 células rodou 4 minutos sem nenhum
falso positivo (incluindo um canal a 13 fps com perda contínua de
quadros, corretamente não sinalizado).

## Fase 4 — Mosaico ✅

`VideoGrid` (`QGridLayout`) com grades 1x1/2x2/3x3/4x4 selecionáveis pela
toolbar. Células endereçadas por um inteiro `position = row * columns + col`
— é isso que a Fase 5 vai persistir. Adicionar câmera por duplo clique
(primeira célula livre) ou drag-and-drop numa célula específica; trocar
câmeras de posição por drag-and-drop. Maximizar com duplo clique,
restaurar com duplo clique ou Esc. Borda discreta na célula selecionada.

### Decisões de implementação

- **Eventos de mouse/teclado sobre o vídeo:** o libVLC captura mouse e
  teclado na própria janela de vídeo, engolindo os eventos que o Qt
  precisa. Resolvido com `video_set_mouse_input(False)` e
  `video_set_key_input(False)`.
- **Maximizar sem reparentar:** ao maximizar, o tile é re-adicionado ao
  *mesmo* layout com `span` cheio, em vez de movido para outro widget.
  Reparentar recriaria a janela X11 e invalidaria o handle que o libVLC
  já está usando. Há teste que verifica que o `winId` não muda.
- **Substream no mosaico:** grades maiores que 1x1 usam substream
  (requisito de desempenho — 16 streams principais seriam banda e
  decodificação desnecessárias para uma célula pequena). Grade 1x1
  respeita o `default_stream` do NVR. A Fase 5 torna isso por célula.
- **Troca de stream ao maximizar:** o substream que serve bem numa célula
  pequena fica visivelmente borrado ocupando a janela inteira, então
  maximizar sobe para o stream principal e restaurar volta ao anterior.
  Cada tile guarda as duas URLs desde a criação, evitando uma nova
  consulta ao keyring a cada maximização. Verificado no equipamento
  real: 640x360 no mosaico → 1280x720 maximizado → 640x360 ao restaurar.
- **Limpeza:** reduzir a grade fecha e libera os players das células que
  deixaram de existir; fechar a janela libera todos.

### Abrir um NVR inteiro (sugestão do usuário, implementada)

Duplo clique na pasta do NVR abre todos os canais habilitados de uma vez,
escolhendo a menor grade que caiba (`smallest_shape_for`) e substituindo o
que estiver na tela — "ver esse NVR" é um comando completo, misturar com
outros equipamentos seria surpreendente. A senha é resolvida **uma vez**
por NVR (`_nvr_password_or_warn`), não por câmera, senão um NVR sem senha
cadastrada geraria 16 diálogos de aviso. Validado com hardware real: a
grade saltou de 2x2 para 4x4 sozinha e 15/16 canais abriram.

### Validação real (NVR Hikvision ao vivo)

| Grade | Resultado |
|-------|-----------|
| 2x2   | 4/4 células reproduzindo, 0 falhas |
| 3x3   | 9/9 células reproduzindo, 0 falhas |
| 4x4   | 15/16 células reproduzindo |

A única célula que falhou (canal 12) foi verificada isoladamente e falha
do mesmo jeito — não há câmera nesse canal do NVR. O CamView tratou o
erro graciosamente: a célula ficou em estado de erro, as outras 15
continuaram reproduzindo, sem travar a interface.

Também verificado: maximizar/restaurar mantém o vídeo rodando, e todos
os players são liberados ao fechar a janela. As resoluções observadas
(640x360, 704x480, 352x240) confirmam que o substream está sendo usado.

## Fase 5 — Layouts salvos ✅

CRUD de layouts persistindo grade + câmera/stream por posição nas tabelas
`layouts` e `layout_items` (criadas na Fase 1). Menu **Layouts** com
"Salvar" (`Ctrl+S`), "Salvar como..." (`Ctrl+Shift+S`),
"Gerenciar layouts..." e a lista dos layouts salvos para carregar num
clique. `LayoutManagerDialog` faz renomear/excluir. Único método novo no
repositório: `update_shape()`, para sobrescrever a grade de um layout
existente.

### Decisões de implementação

- **Salvar grava o stream do mosaico, não o da tela cheia.** Maximizar
  sobe a célula para o stream principal (Fase 4) — isso é estado de
  visualização, não configuração. `VideoGrid.mosaic_stream_type()`
  devolve o stream que a célula usa *no mosaico*, senão salvar com uma
  célula maximizada transformaria aquela câmera em stream principal
  permanentemente.
- **Uma consulta de senha por NVR ao carregar**, não por célula — mesmo
  motivo de `_open_nvr_mosaic`: um layout com 16 câmeras de um NVR sem
  senha geraria 16 avisos idênticos.
- **Carregar tolera câmeras que sumiram**: posições fora da grade, câmeras
  ou NVRs excluídos são contados e reportados na status bar em vez de
  quebrar o carregamento inteiro.
- **`Ctrl+S` sobrescreve o layout carregado sem perguntar**; só pede nome
  quando não há layout ativo. O nome do layout na tela vai para o título
  da janela ("CamView — Fábrica"), e abrir um NVR inteiro limpa esse
  vínculo, já que substitui a tela toda.
- **Menu reconstruído no `aboutToShow`**, porque a lista de layouts muda
  pelo diálogo de gerenciamento e por "Salvar como".

### Validação real (NVR Hikvision ao vivo)

4 câmeras em células não contíguas (0, 1, 4, 5) de uma grade 3x3,
salvas, mosaico limpo e grade forçada para 2x2; ao recarregar o layout a
grade voltou para 3x3 e as **4/4 células voltaram a reproduzir** nas
mesmas posições e resoluções (640x360, 704x480). Layout de teste
removido do banco ao final.

## Fase 6 — Restauração de estado ao abrir ✅

`_save_session()` no `closeEvent` e `_restore_session()` no construtor,
usando a tabela `settings`: geometria da janela (que já inclui o estado
maximizado, via `saveGeometry`), posição de docks/toolbar
(`saveState`), a grade selecionada e o último layout aberto.

### Decisões de implementação

- **Só layouts nomeados voltam.** Um mosaico montado na hora e não salvo
  não é restaurado: reabrir 16 streams que o usuário nunca quis guardar
  seria presunçoso, e o remédio ("salve como layout") é explícito.
- **Cada parte é restaurada de forma independente.** Uma geometria
  corrompida no banco não pode custar o layout do usuário; blobs
  inválidos são logados e ignorados (`_decode`), assim como um id de
  layout que não é número ou que já foi excluído.
- **Restauração é silenciosa** (`_load_layout(..., quiet=True)`): um NVR
  sem senha armazenada logaria um modal por cima de uma janela que ainda
  nem apareceu. O resumo vai para a status bar; os diálogos continuam
  valendo quando o usuário carrega um layout à mão.
- **Salvar sessão nunca impede de fechar o app**: erro de SQLite ali é
  logado e engolido.
- Os blobs binários do Qt são gravados em base64, porque a tabela
  `settings` é texto puro (chave/valor).

### Validação real

Dois processos separados (reinício de verdade) contra o NVR
`192.0.2.3`: o primeiro montou uma grade 3x3 com câmeras nas células
0, 2, 4 e 6, salvou o layout e redimensionou a janela para 1024x640; o
segundo abriu com título, grade, seletor, células e tamanho idênticos e
**4/4 streams reproduzindo**.

## Fase 7 — Tela de configurações ✅

`AppSettings` (`models/settings.py`) reúne tudo que o usuário pode mudar:
latência/`network-caching`, transporte RTSP (TCP/UDP), mudo, reconexão
automática e seu intervalo máximo, stream do mosaico, iniciar maximizado,
reabrir o último layout e o diretório de logs. `services/settings.py`
carrega e grava na tabela `settings`; `SettingsDialog` só edita o valor —
persistir e aplicar é da `MainWindow`, o que deixa o diálogo testável sem
banco.

### Decisões de implementação

- **Leitura tolerante.** `settings_from_mapping()` nunca levanta exceção:
  valor inválido cai no padrão e número fora de faixa é limitado. Uma
  linha editada à mão não pode impedir o app de abrir.
- **Ausência é o padrão.** Salvar remove as linhas que voltaram ao valor
  padrão; para o diretório de logs, "sem linha" é justamente o que
  significa "usar o local padrão".
- **Aplicação a novas conexões.** As opções vão para o libVLC como opções
  de mídia, lidas quando o stream começa — então valem para células
  abertas ou reconectadas dali em diante, não para as que já rodam. A
  status bar diz isso explicitamente.
- **Ordem no `__main__`.** O banco é aberto antes do `QApplication`,
  porque o diretório de logs é uma configuração e o logging é montado
  junto com a aplicação. Diretório inválido cai no padrão em vez de
  abortar a inicialização.
- O combo de stream do mosaico reconstrói o enum explicitamente — mesma
  armadilha do `StreamType` na Fase 2, já que `MosaicStream` também
  herda de `str`.

### Correção do mosaico "picotado" (relato do usuário)

O mosaico usa substream por padrão, e vários equipamentos aqui têm o
substream configurado com taxa de quadros baixa. Medido via ISAPI
(`/ISAPI/Streaming/channels/<N02>`, campo `maxFrameRate` em centésimos):
`192.0.2.6` e `198.51.100.206` entregam 10 fps no substream contra 25 no
principal. Não é bug do app — é configuração do NVR — mas o app precisava
dar saída. Duas foram adicionadas:

1. **Global:** "Stream do mosaico" nas configurações — substream (padrão),
   principal, ou seguir o padrão de cada NVR.
2. **Por célula:** menu de contexto (botão direito) na célula, escolhendo
   principal ou substream. É por célula porque o motivo é por câmera:
   uma câmera picotada não deveria obrigar as outras 15 a subir para
   stream principal. A escolha sobrevive a maximizar/restaurar e é
   gravada no layout (a Fase 5 já persiste stream por posição).

Validação real no `192.0.2.6`, mosaico 2x2:

| Situação | Resultado |
|----------|-----------|
| Substream (padrão) | 4 células a 640x360, **13 fps** |
| Célula 0 trocada para principal | célula 0 a 1920x1080 **29,8 fps**; vizinhas seguem em 13 fps |
| Configuração global = principal | 4 células a 1920x1080, **26–30 fps** |

## Fase 8 — Tratamento de erros ✅

Auditoria caso a caso. Regra aplicada em todos: capturar na camada certa,
logar o detalhe técnico e mostrar ao usuário uma frase que nomeia o que
falhou.

| Cenário | Onde é tratado | O que o usuário vê |
|---------|----------------|--------------------|
| Banco corrompido/inacessível **na abertura** | `__main__._report_startup_failure` | Diálogo com o caminho do arquivo, o erro e a instrução de renomeá-lo; avisa que as senhas seguem no keyring. Sai com código 1 |
| Erro de banco **durante o uso** | `MainWindow._report_error` + guardas em ler NVRs, abrir câmera, abrir NVR inteiro e carregar layout | Diálogo nomeando a operação; o mosaico na tela é preservado |
| Configurações ilegíveis | construtor da `MainWindow` | Nada: cai nos padrões e loga (perder a janela seria pior que perder preferências) |
| Falha ao gravar a sessão | `_save_session` | Nada: logado; nunca impede fechar o app |
| libVLC ausente | `MainWindow._vlc_is_available` | **Um** diálogo com o comando de instalação — não um por célula |
| Keyring indisponível | `_nvr_password_or_warn` | Diálogo; silencioso quando é restauração de sessão |
| Sem senha armazenada | `_nvr_password_or_warn` | Aviso único por NVR, e o stream nem é tentado (evita bloqueio de IP) |
| Endereço inválido (URL colada no lugar do host) | `NvrDialog._on_accept` | Recusa com explicação, antes de salvar |
| Senha em branco no cadastro | `NvrDialog._on_accept` | Pergunta se é intencional, explicando que os streams não abrirão |
| Credenciais erradas / NVR inacessível | `VideoTile._schedule_reconnect` | Após 5 falhas seguidas, a célula passa a sugerir conferir usuário/senha **e avisa do risco de bloqueio do IP** |
| Stream travado sem erro do libVLC | watchdog (`_check_for_stall`) | Reconecta sozinho; ver Fase 3 |
| Codec não suportado / stream indisponível | evento de erro do libVLC | Célula em estado de erro + reconexão com backoff |
| Exceção não tratada em qualquer lugar | `app._install_excepthook` | Registrada no log; o app continua de pé (`KeyboardInterrupt` segue para o hook padrão) |

### Decisões que valem registro

- **Nada de adivinhação prematura.** O libVLC não distingue senha errada de
  equipamento fora do ar, então a dica de credenciais só aparece depois de
  5 falhas consecutivas sem nenhuma reprodução — e some assim que o stream
  volta. Ela existe porque só uma das duas causas tem consequência séria
  aqui: o bloqueio do IP desta máquina, que já aconteceu duas vezes
  durante o desenvolvimento.
- **A reconexão continua infinita.** Parar depois de N tentativas
  "protegeria" o NVR, mas quebraria o caso normal de CFTV: uma câmera que
  cai por uma hora precisa voltar sozinha. O intervalo máximo (Fase 7) é o
  controle certo para isso.
- **Ler antes de destruir.** `_load_layout` resolve câmeras e NVRs no
  banco *antes* de limpar o mosaico, para que uma falha de banco não deixe
  o usuário com a tela vazia e nenhum layout carregado.
- **Uma lacuna real encontrada pelos próprios testes:** um erro de banco
  ao ler as configurações derrubava o construtor da `MainWindow`, ou seja,
  o app inteiro. Agora cai nos padrões.
- **Bug de produção descoberto ao investigar acesso de rede nos testes:**
  a primeira conexão de uma célula é adiada (`QTimer`) até o widget de
  vídeo existir na tela, mas `close_stream()` não cancelava esse
  agendamento. Uma célula fechada logo após ser criada ainda abria o
  stream depois, deixando um player sem dono. O timer agora pertence ao
  tile e é cancelado no fechamento — e a suíte deixou de tocar a rede.

### Investigação da pista "só o substream congela" (inconclusiva)

Medições feitas no `192.0.2.4`, comparando os mesmos canais nos dois
streams e mosaicos cheios:

- 16 células por 90s: **0 congeladas**; por 3 e 4 minutos em outra
  execução: 0 congeladas.
- A única ocorrência real capturada até agora foi uma célula de
  **substream** parada por mais de 2 minutos (registrada na Fase 3).
- **Hipótese descartada por medição:** as mensagens
  `failed to create video output` e `buffer deadlock prevented` do libVLC
  apareceram exatamente **16 vezes para 16 células** numa execução em que
  **todas as 16 exibiram normalmente** — é o ruído de inicialização já
  conhecido (o libVLC tenta caminhos de hardware, falha e cai para
  software), não a causa do congelamento.

Ou seja: o relato do usuário continua plausível, mas a amostra ainda é
pequena demais para afirmar que o substream é a causa. O sintoma já está
coberto pelo watchdog (detecta em 10s e reconecta), então isto fica como
observação, não como pendência bloqueante. Próximo passo, se voltar a
acontecer: rodar a comparação sub × principal por algumas horas.

## Fase 9 — Polimento de UI ✅

Feita *antes* da Fase 8 a pedido do usuário.

- **Ícones do tema do sistema** em toda a interface (`_icon()` na
  `MainWindow` aceita vários nomes e devolve o primeiro que existir, ou um
  `QIcon` vazio — nome ausente vira "sem ícone", nunca imagem quebrada).
  Verificado contra o Breeze real do usuário: os 12 nomes usados existem.
- **Árvore lateral** com ícone de servidor para o NVR e de câmera para o
  canal (apagado quando o canal está desabilitado), mais tooltips com
  `host:porta` e o número do canal.
- **Selo de stream na célula** (`SUB` / `PRINCIPAL`) no cabeçalho: qual
  stream a célula usa não dá para deduzir da imagem, e é exatamente o que
  se pergunta quando uma célula parece mais picotada que as outras. O
  tooltip ensina o atalho (botão direito).
- **Célula vazia** deixou de ser um traço solto: ícone esmaecido e o texto
  "Arraste uma câmera aqui".
- **Toolbar** com "Adicionar NVR", o seletor de mosaico rotulado e
  "Salvar layout", com separadores.
- **Status bar** ganhou um indicador permanente `N/M células`, alimentado
  pelo novo sinal `VideoGrid.contentsChanged`.
- **Diálogo de NVR**: botão de mostrar/ocultar a senha (digitar senha às
  cegas é como se erra a credencial — e credencial errada é o que bloqueia
  o IP desta máquina no equipamento), placeholder de endereço e rótulos
  consistentes com os outros diálogos.
- **Ícone e identidade da aplicação**: `setDesktopFileName("camview")` liga
  a janela ao atalho `.desktop` (`StartupWMClass=camview`), o que dá ao
  gerenciador de tarefas do KDE o ícone e o nome certos.

### Sobre o tema escuro

Nada de cores fixas: a interface usa papéis da paleta (`palette(mid)`,
`palette(highlight)`), então segue o esquema de cores do desktop. Medido na
sessão real do usuário: fundo `#202326` (tema escuro), ícones `breeze-dark`,
destaque `#6c53a6` — tudo chegando ao app.

As únicas cores fixas que sobraram são deliberadas: os três pontos de
status (âmbar/verde/vermelho), que são semânticos e legíveis nos dois
temas, e o texto branco sobre fundo preto da mensagem de erro, que fica
sobre a área de vídeo.

**Limitação conhecida:** só os estilos `Fusion` e `Windows` estão
disponíveis — o Qt embutido no PySide6 não carrega o plugin de estilo
Breeze do sistema (ABI/plugin path separados). Fusion + paleta e ícones do
KDE é o resultado portátil e seguro; forçar o plugin do sistema arriscaria
incompatibilidade binária por um ganho apenas estético.

## Fase 10 — Testes ✅

**324 testes, 92% de cobertura.** Todos os módulos puros e de serviço em
**100%**: repositórios (99%), geração de URL RTSP, credenciais, backoff,
conectividade, descoberta ISAPI, configurações e resolução de paths XDG.

Preenchido nesta fase o que faltava:

- `config.py` (0 → 100%): resolução XDG com `XDG_DATA_HOME`/`XDG_STATE_HOME`
  e o fallback para `~/.local/...`. Todo teste redireciona as variáveis
  para um diretório temporário — um teste que tocasse o caminho real
  poderia escrever no banco de verdade do usuário.
- `logging_setup.py` (42 → 100%) e `app.py` (50 → 97%): o `QApplication`
  é substituído por um dublê, já que só pode existir um por processo.
- `hikvision._fetch` (0 → 100%): mapeamento de 401/403 para "usuário ou
  senha incorretos", demais códigos HTTP e falhas de rede.
- `stream_manager.displayed_picture_count`: o contador do watchdog nunca
  pode levantar exceção — player estranho, sem mídia ou libVLC recusando
  as estatísticas devolvem "desconhecido".
- Drag-and-drop ponta a ponta (árvore → grade, célula → célula), incluindo
  payload desconhecido e drop fora de qualquer célula.
- Workers em `QThread` do diálogo de NVR e o retorno deles para a UI.
- Menu de contexto da célula (a saída para o mosaico picotado).

### Decisões

- **`pytest` roda headless sem configuração**: o `conftest.py` define
  `QT_QPA_PLATFORM=offscreen` antes de qualquer import do PySide6.
- **A suíte não toca a rede.** Isso foi verificado de fato: o bug do
  timer de conexão diferida (Fase 8) foi descoberto justamente porque a
  suíte completa emitia `live555 demux error` — testes tentando falar com
  `192.0.2.10`.
- **Nenhuma senha real**: fixtures usam `"test-password"`/`"senha-falsa"` e
  endereços da faixa de documentação RFC 5737 (`192.0.2.x`).
- **Refatoração exigida por testabilidade:** `VideoTile.build_context_menu()`
  foi separado de `contextMenuEvent`, porque `menu.exec()` abre um modal e
  travava a execução dos testes. Construir e exibir agora são passos
  distintos.
- O que segue sem cobertura é deliberado: pintura de widget, arrastar com
  o mouse de verdade e o `main()` (que constrói `QApplication` real).

## Fase 11 — Empacotamento e documentação final ✅

- **Instalação limpa validada de ponta a ponta**: virtualenv novo,
  `pip install -e ".[dev]"`, import do pacote, entry point `camview`
  criado, e o app abrindo, criando banco e log em diretórios XDG
  isolados antes de fechar sozinho.
- **`packaging/PKGBUILD`** para Arch/EndeavourOS. Depende de `vlc`, não de
  `libvlc` — a diferença entre um pacote que instala "com sucesso" e um
  app que mostra imagem. Usa `pyside6` e `python-keyring` do sistema, para
  o app usar o mesmo Qt do resto do desktop.
- **`packaging/appimage/`** com `AppRun` e `.desktop`. Deliberadamente um
  esqueleto: o ponto espinhoso do AppImage aqui é embutir o libVLC **com
  os plugins** e reapontar `VLC_PLUGIN_PATH` em tempo de execução, já
  documentado no `AppRun` e no `packaging/README.md`.
- **`CHANGELOG.md`** da 0.1.0, incluindo as notas de segurança (senha só
  no keyring, recusa de stream sem senha por causa do bloqueio de IP) e as
  de plataforma (XWayland, plugins do VLC no Arch).
- `README.md` finalizado: instalação, uso, configurações, o que fazer
  quando algo dá errado, e como rodar testes com cobertura.
