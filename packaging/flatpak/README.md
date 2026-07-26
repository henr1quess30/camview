# CamView em Flatpak

Flatpak resolve dois problemas de uma vez: o pacote roda em qualquer
distribuição (não depende da glibc de quem construiu) e a atualização
fica por conta do `flatpak update`.

## Construir

```bash
sudo pacman -S flatpak flatpak-builder    # uma vez
cd packaging/flatpak
./build.sh                                 # instala para o seu usuário
./build.sh --bundle                        # e gera CamView.flatpak para enviar a alguém
```

O script baixa o runtime, resolve as dependências Python e constrói.
**Reserve tempo:** a maior parte é compilar o VLC, e a primeira execução
baixa alguns GB de SDK.

Testar e desinstalar:

```bash
flatpak run io.github.henr1quess30.CamView
flatpak uninstall --user io.github.henr1quess30.CamView
```

## Codecs: a extensão ffmpeg-full

O runtime traz um ffmpeg com codecs reduzidos, e **H.265 não está entre
eles** — que é justamente o que as câmeras usam. O manifesto declara a
extensão `org.freedesktop.Platform.ffmpeg-full`, e o `build.sh` a instala.

O sintoma quando ela falta é cruel de diagnosticar: a câmera conecta, o
app diz "reproduzindo" e o stream encerra na hora, como se o equipamento
tivesse recusado. Medido: sem a extensão, 1 de 4 células mostrava imagem
(a única em H.264); com ela, 4 de 4.

## Por que o PySide6 vem de um BaseApp

PySide6 **não** é instalado do PyPI aqui. O Flathub publica
`io.qt.PySide.BaseApp`, que já traz Qt6 e PySide6 prontos — é o caminho
suportado (o `flatpak-pip-generator` inclusive se recusa a empacotar
PySide6 e aponta para lá) e evita embarcar uma segunda cópia inteira do
Qt. Por isso o runtime é o `org.kde.Platform`, de onde vem o Qt.

O `cleanup-BaseApp.sh` no fim do build remove os módulos PySide que este
app nunca importa (WebEngine, Charts, 3D), que é a maior parte do peso.

## Por que o manifesto compila o VLC

O runtime não traz VLC, e **libvlc sem os plugins não reproduz RTSP** —
o mesmo problema que aparece no Arch quando se instala só o `libvlc`. Por
isso o manifesto constrói:

1. **live555** — o transporte RTSP. Sem ele o libVLC cai nos módulos
   `satip`/`realrtsp` e toda conexão falha. Vem do mirror do VideoLAN, e
   não do site do live555, porque lá só existe o tarball mais recente e
   ele é substituído no lugar, quebrando o checksum.
2. **VLC 3.0.23** — a mesma versão contra a qual o app foi validado, sem
   interface, sem Lua e sem saída de streaming; só a biblioteca, o
   decodificador avcodec e a saída de vídeo xcb.

## Permissões concedidas

| Permissão | Para quê |
|-----------|----------|
| `--share=network` | RTSP das câmeras e HTTP (ISAPI) dos gravadores |
| `--socket=x11` + `--share=ipc` | libVLC 3.x só embute vídeo em janela X11 |
| `--device=dri` | saída de vídeo |
| `--talk-name=org.freedesktop.secrets` | senhas no keyring do sistema |

Não há acesso à pasta pessoal: o banco e os logs ficam em
`~/.var/app/io.github.henr1quess30.CamView/`.

## Migrar dados de uma instalação anterior

O Flatpak tem banco próprio (`~/.var/app/<app-id>/data/camview/`), então
quem já usava o CamView fora dele começaria sem nenhum NVR. Para levar os
cadastros junto:

```bash
./migrate-data.sh
```

As senhas não são copiadas porque não precisam: elas ficam no keyring do
sistema, o mesmo que o Flatpak acessa pelo portal de segredos.

## Publicar no Flathub

1. O repositório precisa estar **público** no GitHub.
2. Adicionar ao menos uma captura de tela e apontar a URL dela no
   `.metainfo.xml` — **use uma fonte de demonstração**, não o mosaico
   real, que contém imagens de câmeras de verdade.
3. Validar os metadados: `appstreamcli validate *.metainfo.xml`.
4. Abrir um PR em https://github.com/flathub/flathub adicionando o
   manifesto. A revisão costuma pedir ajustes de permissão — o conjunto
   acima é enxuto de propósito, o que ajuda.

Depois de aceito, quem instalar recebe atualização pelo `flatpak update`
como qualquer outro app.

## Atualizações fora do Flathub

Quem baixar o `.flatpak` avulso não recebe atualização automática. Para
esses, o próprio CamView avisa: ele consulta as releases publicadas no
GitHub e mostra um aviso discreto na barra inferior quando há versão
nova, com link. Nada é baixado nem instalado sozinho, e a verificação
pode ser desligada em Configurações → Ao abrir.
