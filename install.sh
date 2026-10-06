#!/usr/bin/env bash
# Instalacion sin Docker: deja el diario funcionando en este ordenador.
# Uso:  ./install.sh            (solo prepara y te dice como arrancarlo)
#       ./install.sh --service  (ademas lo deja arrancandose solo al encender)

set -euo pipefail
cd "$(dirname "$0")"

PORT="${DIARIO_PORT:-8300}"
HOST="${DIARIO_HOST:-127.0.0.1}"
WITH_SERVICE=0

for arg in "$@"; do
  case "$arg" in
    --service) WITH_SERVICE=1 ;;
    *) echo "Opcion desconocida: $arg"; exit 1 ;;
  esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[31m%s\033[0m\n\n' "$1"; exit 1; }

command -v python3 >/dev/null 2>&1 || fail "Necesitas Python 3.11 o superior instalado."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || fail "Tu Python es demasiado antiguo: hace falta 3.11 o superior."

step "Preparando el entorno"
if [ ! -d venv ]; then
  python3 -m venv venv
  echo "Entorno virtual creado."
else
  echo "El entorno ya existia, se reutiliza."
fi
venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet -r requirements.txt
echo "Dependencias instaladas."

step "Preparando los datos"
mkdir -p data
echo "Los datos viven en $(pwd)/data/diario.db"

step "Creando el acceso"
venv/bin/python manage.py ensure-user --username "${DIARIO_USER:-admin}"

if [ "$WITH_SERVICE" = "1" ]; then
  if ! command -v systemctl >/dev/null 2>&1; then
    fail "Para instalarlo como servicio hace falta systemd (Linux)."
  fi
  step "Instalando el servicio"
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/diario.service" <<UNIT
[Unit]
Description=Diario de acciones
After=network.target

[Service]
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/venv/bin/python -m uvicorn app.main:app --host $HOST --port $PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now diario.service
  step "Hecho"
  echo "Estado:      systemctl --user status diario"
  echo "Registrar:   journalctl --user -u diario -f"
  echo "Parar:       systemctl --user stop diario"
else
  step "Listo"
  echo "Arranca con:  ./run.sh"
  echo "Y abre:       http://$HOST:$PORT"
  echo
  echo "Para que arranque solo al encender el ordenador:  ./install.sh --service"
fi