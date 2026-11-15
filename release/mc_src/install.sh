#!/usr/bin/env bash
# SafeShell Pro one-command installer / operator.
# Usage:
#   bash install.sh              install (build + up + health check)
#   bash install.sh status       show container health
#   bash install.sh logs         follow logs (Ctrl-C to stop watching)
#   bash install.sh stop         stop and remove containers (keeps data/)
#   bash install.sh restart      restart the service
#   bash install.sh doctor       diagnose docker / WSL / port / network mode
#   bash install.sh keepalive    resident WSL session: keep VM/container alive
#   bash install.sh keepalive-stop   ask the resident loop to exit
#   bash install.sh fix-network  repair WSL dockerd bridge NAT (one-off, idempotent)
#
# All output is ASCII so the companion Windows .bat wrapper stays encoding-safe.

set -euo pipefail
cd "$(dirname "$0")"

APP_PORT="${APP_PORT:-8772}"
COMPOSE="docker compose"
CONTAINER="safeshell-pro-app"

info() { printf '[INFO]  %s\n' "$*"; }
warn() { printf '[WARN]  %s\n' "$*"; }
die()  { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

need_docker() {
  command -v docker >/dev/null 2>&1 || die "docker not found. Install Docker, or start Docker Desktop / WSL dockerd."

  # WSL2 starts a fresh lightweight session per `wsl -e` call; the daemon can take
  # a few seconds to become reachable, and SysV service may need a nudge.
  local i ok=0
  for i in $(seq 1 10); do
    if docker info >/dev/null 2>&1; then ok=1; break; fi
    if [ "$i" = 3 ] && [ "$(id -u)" = 0 ] && command -v service >/dev/null 2>&1; then
      info "starting docker service..."
      service docker start >/dev/null 2>&1 || true
    fi
    sleep 2
  done
  [ "$ok" = 1 ] || die "docker daemon unreachable. In WSL run: service docker start  (or start Docker Desktop)."
  docker compose version >/dev/null 2>&1 || die "docker compose v2 is required (docker compose version)."
}

is_wsl_native_docker() {
  # WSL2 with a native dockerd (not Docker Desktop) often cannot route bridge DNS
  # to the outside world; host networking is required for upstream model calls.
  grep -qi microsoft /proc/version 2>/dev/null || { echo no; return; }
  if docker info --format '{{.OperatingSystem}}' 2>/dev/null | grep -qi 'docker desktop'; then
    echo no
  else
    echo yes
  fi
}

ensure_env() {
  if [ ! -f .env ]; then
    [ -f .env.example ] || die ".env.example missing - cannot generate .env"
    cp .env.example .env
    info "generated .env from .env.example (placeholders only, no real keys)"
  fi
  # 2026-11-15: WSL 原生 dockerd 曾以 --iptables=false 启动，bridge 容器
  # 既出不了网（缺 MASQUERADE）也进不来（缺 DNAT）。修好 dockerd 后统一走
  # 标准 bridge；这里只在 NAT 缺失时兜底修复，不再退化到 host 网络。
  if ! grep -q '^SFP_NETWORK_MODE=' .env; then
    printf '\nSFP_NETWORK_MODE=bridge\n' >> .env
    info "default network mode: bridge"
  fi
  if [ "$(is_wsl_native_docker)" = yes ] && command -v iptables-legacy >/dev/null 2>&1; then
    if ! iptables-legacy -t nat -S POSTROUTING 2>/dev/null | grep -q MASQUERADE; then
      warn "bridge NAT missing - running fix-network"
      cmd_fix_network || true
    fi
  fi
  info "network mode: $(grep '^SFP_NETWORK_MODE=' .env | head -1 | cut -d= -f2)"
}

port_busy() {
  command -v curl >/dev/null 2>&1 || return 1
  curl -s -o /dev/null --max-time 1 "http://127.0.0.1:${APP_PORT}/api/v1/health" 2>/dev/null
}

container_running() {
  # When launched from Windows cmd, wsl.exe output may carry CRLF; strip CR first.
  docker ps --format '{{.Names}}' 2>/dev/null | tr -d '\r' | grep -qx "$CONTAINER"
}

wait_healthy() {
  local i code=000
  for i in $(seq 1 40); do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 \
      "http://127.0.0.1:${APP_PORT}/api/v1/health" 2>/dev/null || echo 000)
    [ "$code" = "200" ] && { info "healthy after ${i} check(s)"; return 0; }
    sleep 2
  done
  warn "health check timed out. Run: bash install.sh logs"
  return 1
}

print_next_steps() {
  printf '\n'
  printf '  Open:  http://127.0.0.1:%s\n' "$APP_PORT"
  printf '  Guide: http://127.0.0.1:%s/#guide   (add your own free API Key)\n' "$APP_PORT"
  printf '  Team:  http://127.0.0.1:%s/#team    (models / policy / audit / cost)\n' "$APP_PORT"
  printf '\n'
  printf '  status: bash install.sh status     stop: bash install.sh stop\n'
  printf '  logs:   bash install.sh logs       diagnose: bash install.sh doctor\n'
  printf '\n'
  printf '  NOTE: a fresh install has NO API keys. Use the web wizard to add one,\n'
  printf '        or copy an existing data/margin.db into ./data before starting.\n'
}

cmd_install() {
  need_docker
  ensure_env
  if container_running; then
    info "container already running - skipping build"
    wait_healthy || true
    print_next_steps
    return 0
  fi
  if port_busy; then
    die "port ${APP_PORT} is used by another process. Set APP_PORT=xxxx and retry."
  fi
  info "building image (first run: 1-3 min, needs network for npm ci / pip install)..."
  $COMPOSE build
  info "starting container..."
  $COMPOSE up -d
  wait_healthy || true
  print_next_steps
}

cmd_doctor() {
  info "platform: $(uname -a)"
  command -v docker >/dev/null 2>&1 && docker --version || warn "docker missing"
  if command -v docker >/dev/null 2>&1; then
    info "daemon OS: $(docker info --format '{{.OperatingSystem}}' 2>/dev/null | tr -d '\r')"
  fi
  info "WSL native dockerd: $(is_wsl_native_docker)"
  [ -f .env ] && info ".env network: $(grep '^SFP_NETWORK_MODE=' .env | head -1)" || warn ".env missing (run install)"
  if container_running; then info "container: running"; else warn "container: not running"; fi
  if port_busy; then info "port ${APP_PORT}: responding"; else warn "port ${APP_PORT}: no response"; fi
  info "credentials in data/: $(ls data/*.db 2>/dev/null | wc -l) db file(s)"
  info "model list: curl -s http://127.0.0.1:${APP_PORT}/api/v1/models"
  info "WSL keepalive: bash keepalive.sh  (or double-click keep-wsl.bat on Windows)"
  if command -v iptables-legacy >/dev/null 2>&1; then
    info "bridge NAT: $(iptables-legacy -t nat -S POSTROUTING 2>/dev/null | grep -c MASQUERADE) MASQUERADE rule(s)"
  fi
}

cmd_fix_network() {
  # WSL 原生 dockerd 用 --iptables=false 启动时不会建 MASQUERADE，
  # bridge 容器 DNS 与直连 IP 全部超时。补一条幂等的源地址转换规则即可，
  # 无需重启 dockerd、不影响已运行的容器。
  need_docker
  command -v iptables-legacy >/dev/null 2>&1 || die "iptables-legacy not found (standard Docker does not need this)"
  local subnet
  subnet=$(docker network inspect bridge \
    --format '{{(index .IPAM.Config 0).Subnet}}' 2>/dev/null | tr -d '\r')
  [ -n "$subnet" ] || subnet=172.17.0.0/16
  if iptables-legacy -t nat -S POSTROUTING 2>/dev/null | grep -q MASQUERADE; then
    info "MASQUERADE already present (dockerd manages iptables normally)"
  elif iptables-legacy -t nat -A POSTROUTING -s "$subnet" -j MASQUERADE 2>/dev/null; then
    info "added MASQUERADE for $subnet - bridge containers can now reach the internet"
  else
    die "cannot add MASQUERADE (need root; in WSL run with sudo)"
  fi
  info "verifying: docker run --rm --network=bridge alpine:3.20 ping -c1 223.5.5.5"
}

case "${1:-install}" in
  install|up|start) cmd_install ;;
  status)  need_docker; $COMPOSE ps; printf '\n'; wait_healthy || true ;;
  logs)    need_docker; $COMPOSE logs -f --tail 100 ;;
  stop|down) need_docker; $COMPOSE down; info "stopped. data/ and logs/ are kept." ;;
  restart) need_docker; $COMPOSE restart; wait_healthy || true ;;
  doctor)  cmd_doctor ;;
  keepalive) exec bash keepalive.sh ;;
  keepalive-once) exec bash keepalive.sh --once ;;
  keepalive-stop) exec bash keepalive.sh --stop ;;
  fix-network) cmd_fix_network ;;
  *) die "unknown command: $1 (use install|status|logs|stop|restart|doctor|keepalive|fix-network)" ;;
esac
