#!/usr/bin/env bash
# One-shot installer for CamView.
#
#   ./install-flatpak.sh CamView.flatpak
#   ./install-flatpak.sh                  # picks CamView.flatpak in this folder
#
# Installs everything the app needs and leaves it working: the KDE
# runtime, the PySide base, and the ffmpeg extension that carries H.265 —
# without which cameras connect and then close instantly.
set -euo pipefail

BUNDLE="${1:-CamView.flatpak}"
APP_ID="io.github.henr1quess30.CamView"
RUNTIME_VERSION="6.9"

die() { printf '\n%s\n' "$*" >&2; exit 1; }

if ! command -v flatpak >/dev/null; then
    cat >&2 <<'EOF'
flatpak não está instalado. Instale pelo gerenciador da sua distribuição:

    Arch/EndeavourOS   sudo pacman -S flatpak
    Ubuntu/Mint/Debian sudo apt install flatpak
    Fedora             já vem instalado

Depois abra e feche a sessão uma vez, e rode este script de novo.
EOF
    exit 1
fi

[ -f "$BUNDLE" ] || die "Arquivo não encontrado: $BUNDLE
Baixe o CamView.flatpak em https://github.com/henr1quess30/camview/releases"

echo "==> Repositório Flathub (de onde vêm as dependências)"
flatpak remote-add --if-not-exists --user flathub \
    https://dl.flathub.org/repo/flathub.flatpakrepo

echo "==> Dependências (baixa alguns GB na primeira vez)"
flatpak install --user --noninteractive flathub \
    "org.kde.Platform//${RUNTIME_VERSION}" \
    "org.freedesktop.Platform.ffmpeg-full//24.08"

echo "==> CamView"
flatpak install --user --noninteractive --bundle "$BUNDLE"

cat <<EOF

Pronto. O CamView já aparece no menu de aplicativos, ou rode:

    flatpak run ${APP_ID}

Para desinstalar:

    flatpak uninstall --user ${APP_ID}
EOF
