#!/usr/bin/env bash
# kick-watcher installer for Linux servers (Amazon Linux / Fedora / RHEL, Debian / Ubuntu).
#
#   curl -fsSL https://raw.githubusercontent.com/josemsalcedoq/kick-watcher/main/install.sh | sudo bash
#   # or, from a clone:
#   sudo ./install.sh
#
# Safe to run again: it updates the code and restarts the service.
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/josemsalcedoq/kick-watcher/main"
APP_DIR=/opt/kick-watcher
DATA_DIR=/var/lib/kick-watcher
SVC_USER=kickwatcher
CHROME_RPM="https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm"
CHROME_DEB="https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Ejecuta con sudo: sudo ./install.sh"
[ "$(uname -s)" = "Linux" ] || die "Este script es para Linux. En Windows usa install.ps1"
[ "$(uname -m)" = "x86_64" ] || die "Google Chrome para Linux solo existe en x86_64 (aquí: $(uname -m))"
command -v systemctl >/dev/null || die "Hace falta systemd"

# ---------------------------------------------------------------- paquetes
if command -v dnf >/dev/null; then
    say "Instalando Chrome, Xvfb y Python (dnf)"
    PY_PKGS="python3 python3-pip"
    dnf -q list --available python3.11 >/dev/null 2>&1 && PY_PKGS="python3.11 python3.11-pip"
    dnf -q list installed python3.11 >/dev/null 2>&1 && PY_PKGS="python3.11 python3.11-pip"
    # Amazon Linux ships curl-minimal, which conflicts with the full curl package
    command -v curl >/dev/null || PY_PKGS="$PY_PKGS curl-minimal"
    # shellcheck disable=SC2086
    dnf -y -q install xorg-x11-server-Xvfb libatomic $PY_PKGS
    command -v google-chrome >/dev/null || dnf -y -q install "$CHROME_RPM"
elif command -v apt-get >/dev/null; then
    say "Instalando Chrome, Xvfb y Python (apt)"
    export DEBIAN_FRONTEND=noninteractive
    apt-get -qq update
    apt-get -qq -y install xvfb xauth curl ca-certificates python3 python3-venv python3-pip libatomic1
    if ! command -v google-chrome >/dev/null; then
        tmp_deb=$(mktemp --suffix=.deb)
        curl -fsSL "$CHROME_DEB" -o "$tmp_deb"
        apt-get -qq -y install "$tmp_deb"
        rm -f "$tmp_deb"
    fi
else
    die "Gestor de paquetes no soportado (hace falta dnf o apt-get)"
fi

PYTHON=$(command -v python3.11 || command -v python3) || die "No encontré Python 3"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 9))' || die "Hace falta Python 3.9 o superior"
command -v xvfb-run >/dev/null || die "No se instaló xvfb-run"
say "Chrome: $(google-chrome --version)   Python: $("$PYTHON" --version)"

# ---------------------------------------------------------------- código
SRC=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/watcher.py" ]; then
    SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
else
    say "Descargando el código desde GitHub"
    SRC=$(mktemp -d)
    trap 'rm -rf "$SRC"' EXIT
    for f in watcher.py kick-watcher.service; do
        curl -fsSL "$REPO_RAW/$f" -o "$SRC/$f"
    done
fi

# ---------------------------------------------------------------- usuario y carpetas
if ! id "$SVC_USER" >/dev/null 2>&1; then
    say "Creando el usuario $SVC_USER"
    NOLOGIN=$(command -v nologin || echo /usr/sbin/nologin)
    useradd --system --create-home --home-dir "$DATA_DIR" --shell "$NOLOGIN" "$SVC_USER"
fi
install -d -o "$SVC_USER" -g "$SVC_USER" -m 755 "$APP_DIR" "$DATA_DIR"

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    say "Creando el entorno de Python"
    runuser -u "$SVC_USER" -- "$PYTHON" -m venv "$APP_DIR/.venv"
fi
say "Instalando Playwright"
runuser -u "$SVC_USER" -- "$APP_DIR/.venv/bin/pip" -q install --disable-pip-version-check --upgrade playwright

install -o "$SVC_USER" -g "$SVC_USER" -m 644 "$SRC/watcher.py" "$APP_DIR/watcher.py"
install -m 644 "$SRC/kick-watcher.service" /etc/systemd/system/kick-watcher.service

# ---------------------------------------------------------------- sesión
for candidate in "$SRC/state.json" /tmp/state.json; do
    if [ -f "$candidate" ]; then
        say "Instalando la sesión de Kick desde $candidate"
        install -o "$SVC_USER" -g "$SVC_USER" -m 600 "$candidate" "$DATA_DIR/state.json"
        [ "$candidate" = /tmp/state.json ] && rm -f "$candidate"
        break
    fi
done

# ---------------------------------------------------------------- servicio
systemctl daemon-reload
if [ -f "$DATA_DIR/state.json" ] || [ -d "$DATA_DIR/profile" ]; then
    systemctl enable -q kick-watcher
    systemctl restart kick-watcher
    say "Servicio kick-watcher en marcha"
    echo "    journalctl -u kick-watcher -f"
    echo "    cat $DATA_DIR/status.json"
else
    systemctl enable -q kick-watcher
    warn "Falta la sesión de Kick. En tu PC ejecuta login.py (o install.ps1 -Login) y luego:"
    echo "    scp state.json ESTE_SERVIDOR:/tmp/state.json"
    echo "    sudo ./install.sh        # la instala y arranca el servicio"
fi
