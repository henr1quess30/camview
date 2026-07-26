# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [Não lançado]

### Adicionado

- **Novo layout em branco** (`Ctrl+N`, menu Layouts e botão no gerenciador
  de layouts): fecha todas as câmeras e deixa o mosaico vazio para montar
  uma composição do zero, mantendo a grade escolhida. Pergunta antes
  quando há câmeras abertas, já que a composição na tela pode nunca ter
  sido salva.

## [0.2.0] — 2026-07-26

### Adicionado

- **Câmera avulsa** como tipo de dispositivo: o cadastro passa a ter um
  seletor **NVR/DVR** ou **Câmera**. Escolhendo câmera, some a quantidade
  de canais, ela aparece como uma única linha na lista lateral (em vez de
  pasta com um item dentro) e o duplo clique **abre em tela cheia**, já
  que não há mosaico a montar. Canal de NVR continua indo para a próxima
  célula livre.
- **Consulta de canais sem transmissão ao próprio NVR.** Depois de duas
  falhas seguidas, o app pergunta ao equipamento (ISAPI) se aquele canal
  está online. Se o NVR responde que não — ou nem lista o canal — a
  célula deixa de tentar a cada poucos segundos, explica o motivo e passa
  a tentar de 5 em 5 minutos, para voltar sozinha quando a câmera for
  consertada.

- **Zoom digital** com a roda do mouse, aproximando o ponto sob o cursor
  (até 8x), e pelos atalhos `+`, `-` e `0`. Com a imagem ampliada,
  **arrastar move a imagem** em vez de mover a célula.
- **Navegação entre câmeras** com uma delas em tela cheia: `→` e `←`
  passam para a próxima e a anterior sem voltar ao mosaico, pulando
  células vazias e dando a volta no fim.
- **Atalhos configuráveis** em Configurações → Atalhos de teclado.

### Corrigido

- **Zoom refeito.** A primeira versão usava o filtro de recorte do libVLC
  e convertia a posição do cursor ignorando as barras pretas da célula:
  ampliava no lugar errado e distorcia a imagem. Agora o zoom é pura
  geometria de widget — a área de vídeo recorta um widget ampliado — o
  que mantém a proporção exata e fixa de verdade o ponto sob o cursor.
- Vazamento de um objeto de mídia do libVLC a cada reconexão. Passava
  despercebido numa sessão, mas uma célula que tenta reconectar a cada
  30s repete isso ~2.900 vezes por dia.
- `QStackedLayout::setCurrentWidget` recebia um widget que não estava mais
  na pilha depois da reestruturação da área de vídeo.

## [0.1.0] — 2026-07-25

Primeira versão utilizável. Cliente desktop de visualização ao vivo de
câmeras/NVRs via RTSP, em mosaico. Validado contra oito NVRs Hikvision
reais, com até 16 streams simultâneos.

### Adicionado

- **Cadastro de NVRs** com teste de conexão e detecção automática de
  canais e nomes de câmera via ISAPI (equipamentos reais têm canais
  faltando; assumir `1..N` cria células mortas).
- **Mosaico** 1x1 a 4x4, com duplo clique e arrastar para posicionar
  câmeras, trocar células de lugar e maximizar. Duplo clique no NVR abre
  todos os canais dele de uma vez, ajustando a grade.
- **Layouts salvos**: nome, grade, câmera e stream por posição, com
  carregar, renomear, sobrescrever e excluir.
- **Restauração de sessão**: janela, grade e último layout voltam como
  estavam, com as câmeras reproduzindo.
- **Configurações**: latência, transporte RTSP, stream do mosaico, áudio,
  reconexão automática e intervalo máximo, iniciar maximizado, reabrir o
  último layout e diretório de logs.
- **Escolha de stream por célula** (botão direito), para contornar
  substreams configurados com taxa de quadros baixa no próprio NVR.
- **Watchdog de travamento**: quando o NVR para de enviar sem avisar — o
  libVLC não emite erro nenhum nesse caso — a célula é reconectada
  sozinha em ~10s.
- **Reconexão com backoff** (2s → 5s → 10s → até o teto configurado), e um
  aviso sobre risco de bloqueio de IP após falhas repetidas.
- Senhas apenas no **keyring** (KWallet/SecretService), nunca no banco nem
  na URL persistida.
- Atalho no menu do KDE (`scripts/install-desktop-entry.sh`) e esqueleto
  de empacotamento (`packaging/`: PKGBUILD do Arch e AppImage).

### Segurança

- O app se recusa a abrir streams de um NVR sem senha armazenada, em vez
  de tentar e falhar: NVRs Hikvision bloqueiam o IP de origem depois de
  algumas tentativas de login malsucedidas.
- Nenhuma senha real nos testes; fixtures usam endereços da faixa de
  documentação RFC 5737.

### Notas de plataforma

- O libVLC 3.x só embute vídeo em janela X11, então o app força
  `QT_QPA_PLATFORM=xcb` e roda via XWayland em sessões Wayland.
- No Arch, o pacote `libvlc` sozinho **não reproduz RTSP**; é preciso o
  pacote `vlc` completo (transporte live555, decodificadores ffmpeg e
  módulos de saída de vídeo são pacotes separados).
