#!/usr/bin/env bash
# Install a KDE/XDG menu entry pointing at this checkout's virtualenv.
#
# Run from anywhere; paths are resolved from the script's own location, so
# the entry keeps working regardless of where the repo lives.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_python="${repo_root}/.venv/bin/python"
target_dir="${XDG_DATA_HOME:-${HOME}/.local/share}/applications"
target="${target_dir}/camview.desktop"

if [[ ! -x "${venv_python}" ]]; then
    echo "Virtualenv não encontrado em ${venv_python}" >&2
    echo "Crie-o primeiro:  python -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'" >&2
    exit 1
fi

mkdir -p "${target_dir}"
sed "s|@EXEC@|${venv_python} -m camview|" \
    "${repo_root}/scripts/camview.desktop.in" > "${target}"

# Refresh the menu cache so the entry shows up without re-login.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${target_dir}" 2>/dev/null || true
fi

echo "Atalho instalado em ${target}"
echo "Procure por 'CamView' no menu de aplicativos."
