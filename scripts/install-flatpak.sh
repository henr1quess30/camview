#!/usr/bin/env bash
# One-shot installer and updater for CamView.
#
#   ./install-flatpak.sh CamView.flatpak
#   ./install-flatpak.sh                  # picks CamView.flatpak in this folder
#
# Installs everything the app needs and leaves it working: the KDE
# runtime, the PySide base, and the ffmpeg extension that carries H.265 —
# without which cameras connect and then close instantly.
#
# Run it again with a newer bundle to update. That needs saying because
# neither obvious route works: a bundle carries no repository to poll, so
# `flatpak update` reports "Nothing to update" forever, and a plain
# `flatpak install --bundle` refuses with "já instalado". Devices,
# layouts and passwords are untouched either way — they live in
# ~/.var/app and the keyring, neither of which reinstalling replaces.
set -euo pipefail

BUNDLE="${1:-CamView.flatpak}"
APP_ID="io.github.henr1quess30.CamView"
RUNTIME_VERSION="6.9"

die() { printf '\n%s\n' "$*" >&2; exit 1; }

# `flatpak info` has no machine-readable version flag on every release —
# --show-version is rejected outright by some — so read the column that
# `list` has always had.
installed_version() {
    flatpak list --app --user --columns=application,version 2>/dev/null \
        | awk -v id="$APP_ID" '$1 == id { print $2 }'
}

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

if flatpak info --user "$APP_ID" >/dev/null 2>&1; then
    INSTALLED=$(installed_version)
    echo "==> Atualizando o CamView (instalado: ${INSTALLED})"
    # Remove and install, rather than --reinstall. --reinstall works on
    # some machines and fails on others with "Diretório não vazio",
    # depending on the flatpak version and on whether the app was running
    # — an upgrade path that works most of the time is worse than one
    # that always does.
    #
    # Nothing of the user's is lost: uninstall without --delete-data
    # leaves ~/.var/app alone, and the passwords were never in the
    # sandbox to begin with — they are in the system keyring, reached
    # through the secrets portal.
    flatpak uninstall --user --noninteractive "$APP_ID"
    flatpak install --user --noninteractive --bundle "$BUNDLE"
    ACTION="atualizado"
else
    echo "==> CamView"
    flatpak install --user --noninteractive --bundle "$BUNDLE"
    ACTION="instalado"
fi

NOW=$(installed_version)

cat <<EOF

CamView ${NOW} ${ACTION}. Ele já aparece no menu de aplicativos, ou rode:

    flatpak run ${APP_ID}

Para desinstalar:

    flatpak uninstall --user ${APP_ID}
EOF
