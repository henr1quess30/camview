#!/usr/bin/env bash
# Run the built AppImage on a *clean* distribution and fail if it cannot
# start.
#
# This exists because the obvious check does not catch the common
# failure. Running with QT_QPA_PLATFORM=offscreen exercises Python, Qt
# and libVLC but never loads the xcb platform plugin, so a bundle missing
# libxcb-cursor passes happily here and dies on the user's desktop with
# "no Qt platform plugin could be initialized". That shipped once.
#
# So: a bare ubuntu:22.04 with nothing but Xvfb — no Qt, no VLC, none of
# the build image's packages — and the real xcb path under a virtual X
# server.
set -euo pipefail

cd "$(dirname "$0")"
APPIMAGE=$(ls -1 dist/CamView-*-x86_64.AppImage 2>/dev/null | head -1)
[ -n "${APPIMAGE}" ] || { echo "Nada em dist/ para verificar." >&2; exit 1; }

RUNNER=$(command -v podman || command -v docker) || {
    echo "podman/docker necessários para verificar." >&2; exit 1; }

echo "==> Verificando $(basename "${APPIMAGE}") num Ubuntu 22.04 limpo"

# --appimage-extract-and-run avoids needing FUSE inside the container;
# the FUSE path is what the user hits, and it is covered separately by
# running the AppImage on the host.
"${RUNNER}" run --rm \
    -v "$(pwd)/${APPIMAGE}":/CamView.AppImage:ro \
    docker.io/library/ubuntu:22.04 bash -c '
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# Xvfb plus the graphics stack, and deliberately nothing else. Every real
# desktop has libEGL/libGL/Mesa — a bare container does not, and the app
# is right not to bundle them — but no desktop ships libxcb-cursor0, Qt6
# or VLC by default, so those stay absent and the bundle has to provide
# them. That gap is exactly what this checks.
apt-get install -y -qq --no-install-recommends \
    xvfb libegl1 libgl1 libglx-mesa0 libglib2.0-0 \
    libfontconfig1 libfreetype6 >/dev/null

# The app has no --version to exit on, so give it a few seconds under a
# virtual display and judge it by what it printed.
timeout 25 xvfb-run -a /CamView.AppImage --appimage-extract-and-run \
    > /tmp/out.log 2>&1 || true

echo "--- saída ---"
cat /tmp/out.log

if grep -qi "no Qt platform plugin could be initialized\|Could not load the Qt platform plugin\|error while loading shared libraries\|ModuleNotFoundError" /tmp/out.log; then
    echo
    echo "FALHOU: o AppImage não inicia numa distribuição limpa."
    exit 1
fi

if ! grep -q "CamView starting up" /tmp/out.log; then
    echo
    echo "FALHOU: o app não chegou a iniciar."
    exit 1
fi

echo
echo "OK: iniciou com o plugin xcb num sistema sem Qt, sem VLC e sem libxcb-cursor."
'
