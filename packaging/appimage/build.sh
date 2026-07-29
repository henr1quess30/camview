#!/usr/bin/env bash
# Build the CamView AppImage.
#
#   ./build.sh
#
# Runs the real work inside a container (see Containerfile) rather than on
# this machine, and the reason is the whole reason an AppImage exists: it
# carries the libraries of whoever built it, and glibc only works
# forwards. Built on Arch — glibc 2.44 — the result starts on Arch and
# refuses everywhere else, which is worse than useless for a format whose
# selling point is "runs anywhere".
set -euo pipefail

cd "$(dirname "$0")"
IMAGE=camview-appimage-builder
OUTPUT="$(pwd)/dist"

die() { printf '\n%s\n' "$*" >&2; exit 1; }

if command -v podman >/dev/null; then
    RUNNER=podman
elif command -v docker >/dev/null; then
    RUNNER=docker
else
    die "Nem podman nem docker encontrados. No Arch/EndeavourOS:

    sudo pacman -S podman

Podman roda sem daemon e sem root, e é só para o build."
fi

echo "==> Imagem de build (${RUNNER})"
"${RUNNER}" build -t "${IMAGE}" -f Containerfile .

# appimagetool is fetched here rather than in the Containerfile so a
# rebuild does not re-download it, and so the container needs no network
# of its own beyond apt.
if [ ! -f appimagetool ]; then
    echo "==> appimagetool"
    curl -sSL -o appimagetool \
        https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x appimagetool
fi

mkdir -p "${OUTPUT}"
echo "==> Construindo"
"${RUNNER}" run --rm \
    -v "$(cd ../.. && pwd)":/src:ro \
    -v "${OUTPUT}":/output:rw \
    -v "$(pwd)/appimagetool":/opt/appimagetool:ro \
    -v "$(pwd)/build-in-container.sh":/build.sh:ro \
    "${IMAGE}" bash /build.sh

cat <<EOF

O AppImage está em ${OUTPUT}/

Para rodar, em qualquer distribuição:

    chmod +x CamView-*-x86_64.AppImage
    ./CamView-*-x86_64.AppImage
EOF
