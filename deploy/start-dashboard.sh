#!/usr/bin/env bash
# ─────────────────────────────────────────────
# Harness-Flow Dashboard 外网部署脚本
# 用法: bash deploy/start-dashboard.sh [start|tunnel|stop|status]
#   start   - 启动 Dashboard + Caddy 反向代理 (局域网访问)
#   tunnel  - 启动 Dashboard + Cloudflare Tunnel (公网穿透)
# ─────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PID_FILE="$PROJECT_ROOT/.dashboard.pid"
CADDY_PID_FILE="$PROJECT_ROOT/.caddy.pid"
CF_PID_FILE="$PROJECT_ROOT/.cloudflared.pid"
CF_URL_FILE="$PROJECT_ROOT/.cloudflared.url"

CADDYFILE="$SCRIPT_DIR/Caddyfile"
DASHBOARD_HOST="127.0.0.1"
DASHBOARD_PORT="8080"
EXTERNAL_PORT="${EXTERNAL_PORT:-8080}"

# ── 颜色 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }

# ── 获取本机 IP（优先公网，回退内网） ──
get_public_ip() {
    local ip=""
    ip=$(curl -s --max-time 3 ifconfig.me 2>/dev/null) \
        || ip=$(curl -s --max-time 3 ipinfo.io/ip 2>/dev/null) \
        || ip=$(curl -s --max-time 3 icanhazip.com 2>/dev/null) \
        || ip=$(curl -s --max-time 3 api.ipify.org 2>/dev/null)
    echo "$ip"
}

get_lan_ip() {
    local ip=""
    if [[ "$(uname -s)" == "Darwin" ]]; then
        ip=$(ipconfig getifaddr en0 2>/dev/null) \
            || ip=$(ipconfig getifaddr en1 2>/dev/null) \
            || ip=$(ifconfig 2>/dev/null | awk '/inet / && !/127\.0\.0\.1/ {print $2; exit}')
    else
        ip=$(hostname -I 2>/dev/null | awk '{print $1}') \
            || ip=$(ip -4 addr show 2>/dev/null | awk '/inet / && !/127\.0\.0\.1/ {print $2; exit}' | cut -d/ -f1) \
            || ip=$(ifconfig 2>/dev/null | awk '/inet / && !/127\.0\.0\.1/ {print $2; exit}')
    fi
    echo "$ip"
}

get_access_url() {
    local public_ip=$(get_public_ip)
    local lan_ip=$(get_lan_ip)

    if [[ -n "$public_ip" ]]; then
        echo "http://${public_ip}:${EXTERNAL_PORT}"
    elif [[ -n "$lan_ip" ]]; then
        echo "http://${lan_ip}:${EXTERNAL_PORT} (内网)"
    else
        echo "http://<无法获取IP>:${EXTERNAL_PORT}"
    fi
}

# ── 依赖检查 ──
check_deps() {
    local mode="${1:-start}"
    local missing=()

    if ! command -v python3 &>/dev/null; then
        missing+=("python3")
    fi
    if ! command -v uvicorn &>/dev/null && ! python3 -c "import uvicorn" 2>/dev/null; then
        missing+=("uvicorn (pip install uvicorn)")
    fi

    if [[ "$mode" != "tunnel" ]]; then
        if ! command -v caddy &>/dev/null; then
            missing+=("caddy")
        fi
    fi

    if [[ "$mode" == "tunnel" ]]; then
        if ! command -v cloudflared &>/dev/null; then
            missing+=("cloudflared")
        fi
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        err "缺少依赖: ${missing[*]}"
        echo ""
        echo "安装指引:"
        echo "  Caddy:         brew install caddy"
        echo "  cloudflared:   brew install cloudflared"
        echo "  uvicorn:       pip install uvicorn fastapi jinja2"
        exit 1
    fi
}

# ── 启动 Dashboard ──
start_dashboard() {
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        warn "Dashboard 已在运行 (PID: $(cat "$PID_FILE"))"
        return 0
    fi

    log "启动 Dashboard (${DASHBOARD_HOST}:${DASHBOARD_PORT})..."
    cd "$PROJECT_ROOT"
    nohup python3 -c "
import uvicorn
from sw_lib.web.app import create_app
uvicorn.run(create_app(), host='${DASHBOARD_HOST}', port=${DASHBOARD_PORT}, log_level='info')
" > /tmp/sw-dashboard.log 2>&1 &

    local pid=$!
    echo "$pid" > "$PID_FILE"

    # 等待启动
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        log "Dashboard 已启动 (PID: $pid)"
    else
        err "Dashboard 启动失败，查看日志: /tmp/sw-dashboard.log"
        rm -f "$PID_FILE"
        exit 1
    fi
}

# ── 启动 Caddy ──
start_caddy() {
    if [[ -f "$CADDY_PID_FILE" ]] && kill -0 "$(cat "$CADDY_PID_FILE")" 2>/dev/null; then
        warn "Caddy 已在运行 (PID: $(cat "$CADDY_PID_FILE"))"
        return 0
    fi

    if [[ ! -f "$CADDYFILE" ]]; then
        err "Caddyfile 不存在: $CADDYFILE"
        exit 1
    fi

    log "启动 Caddy 反向代理 (0.0.0.0:${EXTERNAL_PORT} → ${DASHBOARD_HOST}:${DASHBOARD_PORT})..."
    caddy run --config "$CADDYFILE" --adapter caddyfile &
    local pid=$!
    echo "$pid" > "$CADDY_PID_FILE"

    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        log "Caddy 已启动 (PID: $pid)"
    else
        err "Caddy 启动失败"
        rm -f "$CADDY_PID_FILE"
        exit 1
    fi
}

# ── 启动 Cloudflare Tunnel ──
start_cloudflared() {
    if [[ -f "$CF_PID_FILE" ]] && kill -0 "$(cat "$CF_PID_FILE")" 2>/dev/null; then
        warn "Cloudflare Tunnel 已在运行 (PID: $(cat "$CF_PID_FILE"))"
        return 0
    fi

    log "启动 Cloudflare Tunnel (→ ${DASHBOARD_HOST}:${DASHBOARD_PORT})..."
    rm -f "$CF_URL_FILE"

    cloudflared tunnel --protocol http2 --url "http://${DASHBOARD_HOST}:${DASHBOARD_PORT}" \
        > /tmp/sw-cloudflared.log 2>&1 &
    local pid=$!
    echo "$pid" > "$CF_PID_FILE"

    # 等待隧道就绪（最多 30 秒）
    local waited=0
    while [[ $waited -lt 30 ]]; do
        sleep 1
        waited=$((waited + 1))
        if grep -q 'trycloudflare\.com' /tmp/sw-cloudflared.log 2>/dev/null; then
            local url=$(grep -oE 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' /tmp/sw-cloudflared.log | head -1)
            if [[ -n "$url" ]]; then
                echo "$url" > "$CF_URL_FILE"
                log "Cloudflare Tunnel 已就绪 (PID: $pid)"
                log "外网地址: ${CYAN}${url}${NC}"
                return 0
            fi
        fi
    done

    # 超时还检查一下进程是否活着
    if kill -0 "$pid" 2>/dev/null; then
        warn "Tunnel 进程在运行但未获取到 URL，查看: /tmp/sw-cloudflared.log"
        return 0
    else
        warn "Cloudflare Tunnel 启动失败（cloudflared 可能未安装）"
        rm -f "$CF_PID_FILE"
        return 0
    fi
}

# ── 停止所有服务 ──
stop_all() {
    log "停止服务..."

    if [[ -f "$PID_FILE" ]]; then
        local pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            log "Dashboard 已停止 (PID: $pid)"
        fi
        rm -f "$PID_FILE"
    fi

    if [[ -f "$CADDY_PID_FILE" ]]; then
        local pid=$(cat "$CADDY_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            log "Caddy 已停止 (PID: $pid)"
        fi
        rm -f "$CADDY_PID_FILE"
    fi

    if [[ -f "$CF_PID_FILE" ]]; then
        local pid=$(cat "$CF_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            log "Cloudflare Tunnel 已停止 (PID: $pid)"
        fi
        rm -f "$CF_PID_FILE"
    fi
    rm -f "$CF_URL_FILE"

    log "所有服务已停止"
}

# ── 查看状态 ──
show_status() {
    echo "═══════════════════════════════════════════"
    echo "  Harness-Flow Dashboard 部署状态"
    echo "═══════════════════════════════════════════"

    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo -e "  Dashboard:     ${GREEN}运行中${NC} (PID: $(cat "$PID_FILE"), ${DASHBOARD_HOST}:${DASHBOARD_PORT})"
    else
        echo -e "  Dashboard:     ${RED}未运行${NC}"
    fi

    if [[ -f "$CADDY_PID_FILE" ]] && kill -0 "$(cat "$CADDY_PID_FILE")" 2>/dev/null; then
        echo -e "  Caddy:         ${GREEN}运行中${NC} (PID: $(cat "$CADDY_PID_FILE"), 端口: ${EXTERNAL_PORT})"
    else
        echo -e "  Caddy:         ${RED}未运行${NC}"
    fi

    if [[ -f "$CF_PID_FILE" ]] && kill -0 "$(cat "$CF_PID_FILE")" 2>/dev/null; then
        local cf_url=""
        [[ -f "$CF_URL_FILE" ]] && cf_url=$(cat "$CF_URL_FILE")
        echo -e "  Cloudflare:    ${GREEN}运行中${NC} (PID: $(cat "$CF_PID_FILE"))"
        if [[ -n "$cf_url" ]]; then
            echo -e "  Tunnel URL:    ${CYAN}${cf_url}${NC}"
        fi
    elif [[ ! -f "$CADDY_PID_FILE" ]]; then
        echo -e "  Cloudflare:    ${RED}未运行${NC}"
    fi

    echo ""
    if [[ -f "$CF_URL_FILE" ]]; then
        echo "  外网访问:  $(cat "$CF_URL_FILE")"
    elif [[ -f "$CADDY_PID_FILE" ]] && kill -0 "$(cat "$CADDY_PID_FILE")" 2>/dev/null; then
        echo "  局域网:    $(get_access_url)"
    else
        echo "  访问地址:  未启动"
    fi
    echo "═══════════════════════════════════════════"
}

# ── 主流程 ──
case "${1:-start}" in
    start)
        check_deps start
        start_dashboard
        start_caddy
        start_cloudflared
        echo ""
        show_status
        echo ""
        log "部署完成！使用 'bash deploy/start-dashboard.sh stop' 停止服务"
        disown -a 2>/dev/null || true
        ;;
    tunnel)
        check_deps tunnel
        start_dashboard
        start_cloudflared
        echo ""
        show_status
        echo ""
        log "部署完成！使用 'bash deploy/start-dashboard.sh stop' 停止服务"
        disown -a 2>/dev/null || true
        ;;
    stop)
        stop_all
        ;;
    status)
        show_status
        ;;
    *)
        echo "用法: $0 {start|tunnel|stop|status}"
        echo ""
        echo "  start   - 启动 Dashboard + Caddy 反向代理 (局域网访问)"
        echo "  tunnel  - 启动 Dashboard + Cloudflare Tunnel (公网穿透)"
        echo "  stop    - 停止所有服务"
        echo "  status  - 查看运行状态"
        exit 1
        ;;
esac
