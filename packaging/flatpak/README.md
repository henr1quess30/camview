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
