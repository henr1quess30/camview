# Empacotamento

Duas formas de distribuir o CamView. Nenhuma delas é necessária para usar
o app a partir do checkout — para isso basta o `pip install -e ".[dev]"`
descrito no README principal.

## Pacote do Arch (recomendado nesta máquina)

```bash
cd packaging
makepkg -si
```

O `PKGBUILD` depende de `vlc` (não de `libvlc`): no Arch, `libvlc`
sozinho **não reproduz RTSP** — transporte, decodificadores e módulos de
saída de vídeo são pacotes separados. Depender do `vlc` completo evita
que o app instale "com sucesso" e não mostre imagem nenhuma.

Também usa os pacotes do sistema para `pyside6` e `python-keyring`, em vez
de baixá-los do PyPI: é o que mantém o app usando o mesmo Qt do resto do
desktop.

`source` aponta para uma tag `v$pkgver` no GitHub. Para empacotar a partir
do diretório local, troque por `source=()` e use `makepkg --skipinteg` com
os arquivos copiados à mão.

## AppImage

O `appimage/` guarda o esqueleto (`AppRun` + `.desktop`) para gerar um
binário único que roda em qualquer distribuição. Ainda **não** é um
processo automatizado: falta empacotar o Python, o PySide6 e — o ponto
espinhoso — o libVLC **com seus plugins**, que é justamente o que costuma
quebrar (o plugin path precisa ser reapontado em tempo de execução via
`VLC_PLUGIN_PATH`).

Enquanto isso não é feito, a instalação recomendada fora do Arch é o
virtualenv do README principal.
