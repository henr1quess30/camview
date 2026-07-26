#!/usr/bin/env bash
# Build the CamView Flatpak.
#
#   ./build.sh          build and install for the current user
#   ./build.sh --bundle build and also produce CamView.flatpak to share
#
# Everything happens in this directory: build/, .flatpak-builder/ and the
# generated python3-requirements.json are all disposable.
set -euo pipefail

APP_ID="io.github.henr1quess30.CamView"
MANIFEST="${APP_ID}.yml"
RUNTIME_VERSION="6.9"      # org.kde.Platform / org.kde.Sdk
BASEAPP_VERSION="6.9"      # io.qt.PySide.BaseApp
cd "$(dirname "$0")"

die() { printf '\n%s\n' "$*" >&2; exit 1; }

command -v flatpak >/dev/null || die \
    "flatpak não encontrado. Instale com: sudo pacman -S flatpak flatpak-builder"
command -v flatpak-builder >/dev/null || die \
    "flatpak-builder não encontrado. Instale com: sudo pacman -S flatpak-builder"

echo "==> Runtime e SDK ${RUNTIME_VERSION}"
flatpak remote-add --if-not-exists --user flathub \
    https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user --noninteractive flathub \
    "org.kde.Platform//${RUNTIME_VERSION}" \
    "org.kde.Sdk//${RUNTIME_VERSION}" \
    "io.qt.PySide.BaseApp//${BASEAPP_VERSION}"

# flatpak-builder builds offline, so every wheel must be declared with its
# checksum up front. flatpak-pip-generator resolves requirements.txt into
# exactly that. It is a single script from flatpak-builder-tools.
if [ ! -f python3-requirements.json ] || [ requirements.txt -nt python3-requirements.json ]; then
    echo "==> Resolvendo dependências Python (flatpak-pip-generator)"
    if [ ! -f flatpak-pip-generator ]; then
        # The plain name in that repo is a symlink; fetching it over raw
        # gives the link's text, not the script. Take the .py directly.
        curl -sSL -o flatpak-pip-generator \
            https://raw.githubusercontent.com/flatpak/flatpak-builder-tools/master/pip/flatpak-pip-generator.py
        chmod +x flatpak-pip-generator
    fi
    # The generator is a PEP 723 script with its own dependency, so it
    # gets a throwaway virtualenv rather than touching the system Python.
    if [ ! -x .gen-venv/bin/python ]; then
        python3 -m venv .gen-venv
        .gen-venv/bin/pip install --quiet requirements-parser
    fi
    # --runtime makes it resolve wheels for the runtime's Python, not the
    # host's — the ABI tags have to match or nothing imports.
    .gen-venv/bin/python flatpak-pip-generator \
        --runtime="org.kde.Sdk//${RUNTIME_VERSION}" \
        --requirements-file=requirements.txt \
        --output=python3-requirements
fi

echo "==> Construindo (o VLC leva a maior parte do tempo)"
flatpak-builder --force-clean --user --install-deps-from=flathub \
    --repo=repo --install build "${MANIFEST}"

if [ "${1:-}" = "--bundle" ]; then
    echo "==> Gerando CamView.flatpak"
    flatpak build-bundle repo CamView.flatpak "${APP_ID}"
    echo "    pronto: $(pwd)/CamView.flatpak"
fi

cat <<EOF

Pronto. Para executar:

    flatpak run ${APP_ID}

Para desinstalar:

    flatpak uninstall --user ${APP_ID}
EOF
