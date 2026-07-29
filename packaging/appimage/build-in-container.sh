#!/usr/bin/env bash
# Assemble the AppDir and squash it into an AppImage. Runs *inside* the
# container built from Containerfile — see build.sh for the outside half.
#
# Everything the app needs is copied in: the Python interpreter and its
# standard library, the wheels, libVLC *and its plugin tree*, and the
# shared libraries all of those resolve to. Nothing is assumed present on
# the machine that will run it.
set -euo pipefail

PYTHON=python3.12
APPDIR=/build/AppDir
SOURCE=/src
OUTPUT=/output

rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/bin" "${APPDIR}/usr/lib"

echo "==> Python ${PYTHON} e biblioteca padrão"
PY_PREFIX=$(${PYTHON} -c 'import sys; print(sys.base_prefix)')
PY_TAG=$(${PYTHON} -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")')
cp "$(command -v ${PYTHON})" "${APPDIR}/usr/bin/python3"
mkdir -p "${APPDIR}/usr/lib/${PY_TAG}"
# -a preserves the symlinks inside lib-dynload; dereferencing them would
# double the size for nothing.
cp -a "${PY_PREFIX}/lib/${PY_TAG}/." "${APPDIR}/usr/lib/${PY_TAG}/"
rm -rf "${APPDIR}/usr/lib/${PY_TAG}/test" \
       "${APPDIR}/usr/lib/${PY_TAG}/idlelib" \
       "${APPDIR}/usr/lib/${PY_TAG}/tkinter" \
       "${APPDIR}/usr/lib/${PY_TAG}/turtledemo"

echo "==> Dependências de execução (wheels)"
SITE="${APPDIR}/usr/lib/${PY_TAG}/site-packages"
mkdir -p "${SITE}"
# pip comes from a throwaway 3.12 venv, never from the distribution: the
# system pip belongs to 22.04's Python 3.10 and dies importing distutils,
# which 3.12 removed. The venv is only the tool — the wheels land in the
# AppDir via --target, because a venv's absolute paths would stop meaning
# anything once the AppImage is mounted elsewhere.
${PYTHON} -m venv /tmp/pip-env
/tmp/pip-env/bin/pip install --quiet --upgrade pip
/tmp/pip-env/bin/pip install --quiet --target="${SITE}" \
    "PySide6>=6.7" "python-vlc>=3.0.20123" "keyring>=25.0"

# Qt ships every module; CamView draws widgets and nothing more. Dropping
# the rest is the difference between a ~600 MB AppImage and a usable one.
echo "==> Removendo módulos Qt que o app não usa"
QT="${SITE}/PySide6"
if [ -d "${QT}" ]; then
    find "${QT}" -maxdepth 1 -name 'Qt3D*' -o -maxdepth 1 -name 'QtQuick*' \
        -o -maxdepth 1 -name 'QtQml*' -o -maxdepth 1 -name 'QtWeb*' \
        -o -maxdepth 1 -name 'QtCharts*' -o -maxdepth 1 -name 'QtDataVisualization*' \
        -o -maxdepth 1 -name 'QtMultimedia*' -o -maxdepth 1 -name 'QtSensors*' \
        -o -maxdepth 1 -name 'QtBluetooth*' -o -maxdepth 1 -name 'QtNfc*' \
        -o -maxdepth 1 -name 'QtRemoteObjects*' -o -maxdepth 1 -name 'QtScxml*' \
        -o -maxdepth 1 -name 'QtSpatialAudio*' -o -maxdepth 1 -name 'QtTextToSpeech*' \
        | xargs -r rm -rf
    for lib in Qt6Quick Qt6Qml Qt6WebEngine Qt6Multimedia Qt6Charts Qt63D \
               Qt6DataVisualization Qt6Sensors Qt6Bluetooth Qt6Nfc \
               Qt6RemoteObjects Qt6Scxml Qt6SpatialAudio Qt6TextToSpeech; do
        find "${QT}/Qt/lib" -maxdepth 1 -name "lib${lib}*" -delete 2>/dev/null || true
    done
    rm -rf "${QT}/Qt/qml" "${QT}/Qt/translations" "${QT}/examples" \
           "${QT}/Qt/plugins/qmltooling" 2>/dev/null || true
fi

echo "==> CamView"
cp -r "${SOURCE}/src/camview" "${SITE}/"
find "${SITE}/camview" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

echo "==> libVLC e a árvore de plugins"
VLC_LIBDIR=$(dirname "$(find /usr/lib -name 'libvlc.so*' | head -1)")
cp -a "${VLC_LIBDIR}"/libvlc.so* "${APPDIR}/usr/lib/"
cp -a "${VLC_LIBDIR}"/libvlccore.so* "${APPDIR}/usr/lib/"
# The plugin tree is the part a naive bundle forgets, and its absence is
# invisible until a camera connects and shows nothing.
cp -a "${VLC_LIBDIR}/vlc" "${APPDIR}/usr/lib/"

echo "==> Bibliotecas compartilhadas de que tudo isso depende"
# Walked with ldd over every ELF in the AppDir, repeatedly, because the
# plugins pull in decoders that pull in their own codecs. Errors are
# expected and ignored throughout: the AppDir is full of .py files and
# scripts that ldd rightly refuses, and under `set -e` one of those
# refusals would kill the build with no message at all.
#
# What is deliberately *not* copied is as important as what is. The C
# library, libstdc++ and the graphics stack have to come from the host —
# bundling a Mesa or a libGL that disagrees with the running driver is
# how an AppImage ends up with a black window on hardware that works.
# Fontconfig and FreeType are excluded for a related reason: 22.04's
# fontconfig cannot parse the configuration a current distribution
# writes, and spends the app's first seconds printing parse errors about
# the host's own font setup.
#
# Note the exclusion is libxcb.so itself, not libxcb-*. The helpers —
# libxcb-cursor above all, which Qt 6.5+ requires for the xcb platform
# plugin — are ordinary libraries that a desktop may simply not have
# installed, and leaving them out is why an otherwise complete bundle
# dies with "no Qt platform plugin could be initialized".
is_excluded() {
    case "$1" in
        libc.so*|libm.so*|libdl.so*|libpthread.so*|librt.so*|libresolv.so*|\
        libGL.so*|libGLX.so*|libGLdispatch.so*|libEGL.so*|libgbm.so*|\
        libdrm.so*|libX11.so*|libX11-xcb.so*|libxcb.so*|\
        libgcc_s.so*|libstdc++.so*|ld-linux*|libnvidia*|libcuda*|\
        libfontconfig.so*|libfreetype.so*)
            return 0 ;;
    esac
    return 1
}

collect_libs() {
    set +e +o pipefail   # ldd fails on every non-ELF file it is handed
    local pass before after lib base
    for pass in 1 2 3 4 5 6 7 8; do
        before=$(find "${APPDIR}/usr/lib" -maxdepth 1 -name '*.so*' | wc -l)

        find "${APPDIR}" -type f \( -name '*.so' -o -name '*.so.*' -o -perm -u+x \) \
            -exec ldd {} + 2>/dev/null \
            | awk '/=> \//{print $3}' | sort -u > /tmp/needed.txt

        while IFS= read -r lib; do
            [ -n "${lib}" ] || continue
            base=$(basename "${lib}")
            is_excluded "${base}" && continue
            [ -e "${APPDIR}/usr/lib/${base}" ] && continue
            cp -L "${lib}" "${APPDIR}/usr/lib/${base}" 2>/dev/null
        done < /tmp/needed.txt

        after=$(find "${APPDIR}/usr/lib" -maxdepth 1 -name '*.so*' | wc -l)
        echo "    passada ${pass}: ${before} -> ${after} bibliotecas"
        [ "${before}" = "${after}" ] && break
    done
    set -eo pipefail
}
collect_libs

LIB_COUNT=$(find "${APPDIR}/usr/lib" -maxdepth 1 -name '*.so*' | wc -l)
[ "${LIB_COUNT}" -gt 20 ] || {
    echo "ERRO: só ${LIB_COUNT} bibliotecas coletadas — o fecho falhou." >&2
    exit 1
}

echo "==> Metadados do aplicativo"
install -Dm755 "${SOURCE}/packaging/appimage/AppRun" "${APPDIR}/AppRun"
install -Dm644 "${SOURCE}/packaging/appimage/camview.desktop" \
    "${APPDIR}/camview.desktop"
install -Dm644 "${SOURCE}/packaging/flatpak/io.github.henr1quess30.CamView.svg" \
    "${APPDIR}/camview.svg"
# appimagetool wants the icon at the top level under the name the desktop
# entry gives, and a .DirIcon beside it.
cp "${APPDIR}/camview.svg" "${APPDIR}/.DirIcon"
install -Dm644 "${SOURCE}/packaging/flatpak/io.github.henr1quess30.CamView.svg" \
    "${APPDIR}/usr/share/icons/hicolor/scalable/apps/camview.svg"

echo "==> Teste de sanidade do AppDir"
# Imports, off-screen, using only what was bundled. Catches a missing
# library here rather than on the user's machine, where the symptom would
# be a window that never appears.
env -i HOME=/tmp \
    PYTHONHOME="${APPDIR}/usr" \
    PYTHONPATH="${SITE}" \
    LD_LIBRARY_PATH="${APPDIR}/usr/lib:${SITE}/PySide6/Qt/lib" \
    QT_PLUGIN_PATH="${SITE}/PySide6/Qt/plugins" \
    QT_QPA_PLATFORM=offscreen \
    VLC_PLUGIN_PATH="${APPDIR}/usr/lib/vlc/plugins" \
    "${APPDIR}/usr/bin/python3" - <<'PY'
import sys

from PySide6.QtWidgets import QApplication
import vlc

import camview
from camview.ui.dialogs.device_manager_dialog import DeviceManagerDialog

app = QApplication([])
instance = vlc.Instance("--no-xlib")
if instance is None:
    sys.exit("libVLC não inicializou — árvore de plugins incompleta")
print(f"    CamView {camview.__version__}, Qt e libVLC carregados")
PY

echo "==> Empacotando"
VERSION=$(${PYTHON} - <<'PY'
import re, pathlib
text = pathlib.Path("/src/pyproject.toml").read_text()
print(re.search(r'^version = "([^"]+)"', text, re.M).group(1))
PY
)
mkdir -p "${OUTPUT}"
# No FUSE inside the container, so appimagetool runs extracted.
ARCH=x86_64 /opt/appimagetool --appimage-extract-and-run \
    "${APPDIR}" "${OUTPUT}/CamView-${VERSION}-x86_64.AppImage"

chmod +x "${OUTPUT}/CamView-${VERSION}-x86_64.AppImage"
echo
echo "Pronto: CamView-${VERSION}-x86_64.AppImage"
ls -lh "${OUTPUT}/CamView-${VERSION}-x86_64.AppImage"
