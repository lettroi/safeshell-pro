#!/usr/bin/env bash
# SafeShell Pro - WSL keepalive / watchdog.
#
# Why this exists: with WSL2 native dockerd we must run the container on the
# host network (bridge DNS cannot reach model vendors). WSL shuts the VM down
# shortly after the last interactive session exits, which restarts a host-network
# container and shows intermittent 502 from Windows. Keeping ONE resident WSL
# session alive keeps the VM, dockerd and the container up.
#
# Usage (from a WSL terminal, or double-click keep-wsl.bat on Windows):
#   bash keepalive.sh            # stay resident, self-heal every 30s
#   bash keepalive.sh --once     # one health/self-heal pass, then exit
#   bash keepalive.sh --stop     # ask the resident loop to exit
#
# All output is ASCII so the Windows .bat wrapper stays encoding-safe.

cd "$(dirname "$0")" || exit 1

APP_PORT="${APP_PORT:-8772}"
CONTAINER="safeshell-pro-app"
INTERVAL="${KEEPALIVE_INTERVAL:-30}"
FLAG=".keepalive.stop"

# docker compose 工作目录：优先环境变量，其次当前目录若含 compose 文件，
# 最后回退到已知仓库路径。这样无论 keepalive.sh 被放在哪个目录双击，
# `docker compose up -d` 都能找到 docker-compose.yml。
PROJECT_DIR="${SFP_COMPOSE_DIR:-}"
if [ -z "$PROJECT_DIR" ] && [ -f docker-compose.yml ]; then
  PROJECT_DIR="$(pwd)"
fi
[ -z "$PROJECT_DIR" ] && PROJECT_DIR="/root/safeshell-pro/release/mc_src"

log() { printf '[keepalive %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

ensure_daemon() {
  if docker info >/dev/null 2>&1; then return 0; fi
  log "dockerd not reachable - starting service"
  if [ "$(id -u)" = 0 ] && command -v service >/dev/null 2>&1; then
    service docker start >/dev/null 2>&1 || true
  fi
  sleep 3
  docker info >/dev/null 2>&1
}

ensure_container() {
  if docker ps --format '{{.Names}}' 2>/dev/null | tr -d '\r' | grep -qx "$CONTAINER"; then
    return 0
  fi
  log "container missing - bringing it back with docker compose up -d (dir=$PROJECT_DIR)"
  ( cd "$PROJECT_DIR" && docker compose up -d ) >/dev/null 2>&1 || true
}

ensure_nat() {
  # WSL 原生 dockerd 常以 --iptables=false 启动，不建 MASQUERADE，
  # bridge 容器会整个出不了网（DNS + 直连 IP 全超时）。幂等补规则。
  # 非 root 或标准 Docker Desktop 环境会静默跳过，不影响主流程。
  command -v iptables-legacy >/dev/null 2>&1 || return 0
  local subnet
  subnet=$(docker network inspect bridge \
    --format '{{(index .IPAM.Config 0).Subnet}}' 2>/dev/null | tr -d '\r')
  [ -n "$subnet" ] || subnet=172.17.0.0/16
  iptables-legacy -t nat -S POSTROUTING 2>/dev/null | grep -q MASQUERADE \
    || iptables-legacy -t nat -A POSTROUTING -s "$subnet" -j MASQUERADE 2>/dev/null || true
}

one_pass() {
  if ! ensure_daemon; then log "WARN docker daemon still unreachable"; return 1; fi
  ensure_nat
  ensure_container
  if curl -fsS --max-time 3 "http://127.0.0.1:${APP_PORT}/api/v1/health" >/dev/null 2>&1; then
    log "OK  service healthy on ${APP_PORT}"
  else
    log "WARN health check not ready yet (container may still be starting)"
  fi
}

case "${1:-}" in
  --once)
    one_pass
    exit $?
    ;;
  --stop)
    touch "$FLAG"
    log "stop flag written; resident loop will exit within ${INTERVAL}s"
    exit 0
    ;;
esac

rm -f "$FLAG"
log "resident session started. Keep this window open (minimize is fine)."
log "watching dockerd + container + health every ${INTERVAL}s; Ctrl-C to stop."
while true; do
  [ -f "$FLAG" ] && { rm -f "$FLAG"; log "stop flag received - exiting"; break; }
  one_pass || true
  sleep "$INTERVAL"
done
