#!/usr/bin/env bash
# Plateful - Proxmox VE helper script.
# Run on the Proxmox NODE shell (as root). Creates a small Debian 12 LXC container, installs
# Plateful as a systemd service, and prints the URL.
#
#   From GitHub:   PLATEFUL_REPO=youruser/plateful bash -c "$(curl -fsSL https://raw.githubusercontent.com/youruser/plateful/main/install/plateful.sh)"
#   From a file:   PLATEFUL_TARBALL=/root/plateful.tar.gz bash plateful.sh
#   Update later:  bash plateful.sh update <CTID>      (same env vars as above)
#
# Optional env overrides: CTID CT_NAME CORES RAM DISK BRIDGE STORAGE TEMPLATE_STORAGE PORT BRANCH
set -euo pipefail

APP=plateful
CT_NAME="${CT_NAME:-plateful}"
CORES="${CORES:-1}"
RAM="${RAM:-512}"
DISK="${DISK:-4}"
BRIDGE="${BRIDGE:-vmbr0}"
PORT="${PORT:-8080}"
TRUST_PROXY="${TRUST_PROXY:-0}"   # set to 1 only when behind your own HTTPS reverse proxy/tunnel
BRANCH="${BRANCH:-main}"
PLATEFUL_REPO="${PLATEFUL_REPO:-}"
PLATEFUL_TARBALL="${PLATEFUL_TARBALL:-}"

msg()  { echo -e "\e[1;32m[+]\e[0m $*"; }
warn() { echo -e "\e[1;33m[!]\e[0m $*"; }
die()  { echo -e "\e[1;31m[x]\e[0m $*" >&2; exit 1; }

command -v pct >/dev/null   || die "Run this on a Proxmox VE node (pct not found)."
[ "$(id -u)" -eq 0 ]        || die "Run as root."
[ -n "$PLATEFUL_REPO$PLATEFUL_TARBALL" ] || die "Set PLATEFUL_REPO=user/repo (GitHub) or PLATEFUL_TARBALL=/path/plateful.tar.gz"
[ -z "$PLATEFUL_TARBALL" ] || [ -f "$PLATEFUL_TARBALL" ] || die "Tarball not found: $PLATEFUL_TARBALL"

# Copies the app code into container $1 (preserves the data directory).
deploy_app() {
  local id="$1"
  if [ -n "$PLATEFUL_TARBALL" ]; then
    pct push "$id" "$PLATEFUL_TARBALL" /tmp/plateful.tar.gz
  else
    pct exec "$id" -- bash -c "curl -fsSL https://github.com/${PLATEFUL_REPO}/archive/refs/heads/${BRANCH}.tar.gz -o /tmp/plateful.tar.gz"
  fi
  pct exec "$id" -- bash -c '
    set -e
    rm -rf /tmp/pl && mkdir -p /tmp/pl /opt/plateful
    tar -xzf /tmp/plateful.tar.gz -C /tmp/pl
    SRC=$(find /tmp/pl -name server.py -not -path "*/node_modules/*" | head -n1 | xargs dirname)
    [ -n "$SRC" ] || { echo "server.py not found in archive"; exit 1; }
    cp -r "$SRC"/server.py "$SRC"/seed.py "$SRC"/seed_extra.py "$SRC"/static /opt/plateful/
    mkdir -p /opt/plateful/data
    rm -rf /tmp/pl /tmp/plateful.tar.gz'
}

# ------------------------------------------------------------------ update mode
if [ "${1:-}" = "update" ]; then
  id="${2:-}"; [ -n "$id" ] || die "Usage: plateful.sh update <CTID>"
  msg "Updating Plateful in container $id"
  deploy_app "$id"
  pct exec "$id" -- systemctl restart plateful
  msg "Updated. Your data was kept."
  exit 0
fi

# ------------------------------------------------------------------ create container
CTID="${CTID:-$(pvesh get /cluster/nextid)}"
STORAGE="${STORAGE:-$(pvesm status -content rootdir | awk 'NR>1{print $1; exit}')}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-$(pvesm status -content vztmpl | awk 'NR>1{print $1; exit}')}"
[ -n "$STORAGE" ] || die "No storage with 'rootdir' content found. Set STORAGE=<name>."
[ -n "$TEMPLATE_STORAGE" ] || die "No storage with 'vztmpl' content found. Set TEMPLATE_STORAGE=<name>."

msg "Container ID: $CTID | storage: $STORAGE | templates: $TEMPLATE_STORAGE | bridge: $BRIDGE"

msg "Finding Debian 12 template"
pveam update >/dev/null 2>&1 || warn "pveam update failed (continuing with cached list)"
TEMPLATE="$(pveam list "$TEMPLATE_STORAGE" | awk '/debian-12-standard/{print $1}' | sed 's|.*/||' | sort -V | tail -n1)"
if [ -z "$TEMPLATE" ]; then
  TEMPLATE="$(pveam available --section system | awk '/debian-12-standard/{print $2}' | sort -V | tail -n1)"
  [ -n "$TEMPLATE" ] || die "Could not find a Debian 12 template via pveam."
  msg "Downloading $TEMPLATE"
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi

msg "Creating LXC $CTID ($CT_NAME)"
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
  --hostname "$CT_NAME"--cores "$CORES" --memory "$RAM" --swap 256 \
  --rootfs "$STORAGE:$DISK" --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" \
  --unprivileged 1 --features nesting=1 --onboot 1 --ostype debian \
  --description "Plateful meal planner (http port $PORT)"
pct start "$CTID"

msg "Waiting for network"
IP=""
for _ in $(seq 1 40); do
  IP="$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || true)"
  [ -n "$IP" ] && break
  sleep 2
done
[ -n "$IP" ] || die "Container got no IP. Check bridge '$BRIDGE' and DHCP."

msg "Installing Python"
pct exec "$CTID" -- bash -c "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq python3 curl ca-certificates >/dev/null"

msg "Installing Plateful"
deploy_app "$CTID"

pct exec "$CTID" -- bash -c "cat > /etc/systemd/system/plateful.service <<EOF
[Unit]
Description=Plateful meal planner
After=network.target

[Service]
WorkingDirectory=/opt/plateful
Environment=PLATEFUL_PORT=$PORT
Environment=PLATEFUL_HOST=0.0.0.0
Environment=PLATEFUL_TRUST_PROXY=$TRUST_PROXY
Environment=PLATEFUL_DB=/opt/plateful/data/plateful.db
ExecStart=/usr/bin/python3 /opt/plateful/server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable --now plateful"

sleep 2
if pct exec "$CTID" -- curl -fsS "http://127.0.0.1:$PORT/api/bootstrap" >/dev/null; then
  msg "Plateful is running."
else
  warn "Service did not answer yet - check: pct exec $CTID -- journalctl -u plateful -n 50"
fi
echo
echo -e "  \e[1mOpen:\e[0m  http://$IP:$PORT"
echo "  Tip: set a DHCP reservation for $IP on your router so the address never changes."
echo "  Update later:  bash plateful.sh update $CTID"
