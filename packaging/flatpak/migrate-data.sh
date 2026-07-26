#!/usr/bin/env bash
# Copy an existing CamView database into the Flatpak's sandboxed data
# directory, so a user who already had the app keeps their devices and
# layouts instead of starting over.
#
# Passwords are NOT copied: they live in the system keyring, which the
# Flatpak reaches through the secrets portal — the same store, so they
# are already there.
set -euo pipefail

APP_ID="io.github.henr1quess30.CamView"
SOURCE="${XDG_DATA_HOME:-$HOME/.local/share}/camview/camview.db"
TARGET_DIR="$HOME/.var/app/${APP_ID}/data/camview"
TARGET="${TARGET_DIR}/camview.db"

[ -f "$SOURCE" ] || { echo "Nada a migrar: $SOURCE não existe."; exit 0; }

if [ -f "$TARGET" ]; then
    BACKUP="${TARGET}.bak-$(date +%Y%m%d-%H%M%S)"
    cp "$TARGET" "$BACKUP"
    echo "Banco do Flatpak já existia; copiado para $BACKUP"
fi

mkdir -p "$TARGET_DIR"
cp "$SOURCE" "$TARGET"
echo "Migrado: $SOURCE"
echo "     -> $TARGET"
echo
echo "As senhas continuam no keyring do sistema e são acessadas pelo portal."
