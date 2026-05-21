#!/usr/bin/env bash
# ─────────────────────────────────────────────
# Harness-Flow Dashboard 守护进程部署脚本
#
# 用法: bash deploy/start-dashboard.sh <command>
#
#   install  - 安装为守护进程（Linux→systemd / macOS→launchd）
#   start    - 启动 Dashboard 服务
#   stop     - 停止 Dashboard 服务
#   restart  - 重启 Dashboard 服务
#   status   - 查看运行状态
#   logs     - 查看实时日志
#   uninstall- 停止并移除守护进程服务
#   tunnel   - 启动 + Cloudflare Tunnel 公网穿透
#
# Linux 使用 systemd (/etc/systemd/system/)
# macOS 使用 launchd (~/Library/LaunchAgents/)
# ─────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PID_FILE="$PROJECT_ROOT/.dashboard.pid"
CADDY_PID_FILE="$PROJECT_ROOT/.caddy.pid"
CF_PID_FILE="$PROJECT_ROOT/.cloudflared.pid"
CF_URL_FILE="$PROJECT_ROOT/.cloudflared.url"
CF_MONITOR_PID_FILE="$PROJECT_ROOT/.cloudflared-monitor.pid"
LOG_FILE="/tmp/sw-dashboard.log"

CADDYFILE="$SCRIPT_DIR/Caddyfile"
DASHBOARD_HOST="${DASHBOARD_HOST:-0.0.0.0}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8080}"
EXTERNAL_PORT="${EXTERNAL_PORT:-8080}"

SERVICE_NAME="harness-flow-dashboard"
PLIST_NAME="com.harnessflow.dashboard"
SYSTEMD_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
LAUNCHD_FILE="${HOME}/Library/LaunchAgents/${PLIST_NAME}.plist"
PYTHON_BIN="${PYTHON_BIN:-$(which python3 2>/dev/null || echo python3)}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }

UNAME="$(uname -s)"
has_systemd() { [[ "$UNAME" == "Linux" ]] && command -v systemctl &>/dev/null && [[ -d /run/systemd/system ]]; }
has_launchd() { [[ "$UNAME" == "Darwin" ]] && command -v launchctl &>/dev/null; }

# ── IP 检测 ──
get_public_ip() { curl -s --max-time 3 ifconfig.me 2>/dev/null || curl -s --max-time 3 ipinfo.io/ip 2>/dev/null || curl -s --max-time 3 icanhazip.com 2>/dev/null || curl -s --max-time 3 api.ipify.org 2>/dev/null; }

get_lan_ip() {
    if [[ "$UNAME" == "Darwin" ]]; then
        ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || ifconfig 2>/dev/null | awk '/inet / && !/127\.0\.0\.1/ {print $2; exit}'
    else
        hostname -I 2>/dev/null | awk '{print $1}' || ip -4 addr show 2>/dev/null | awk '/inet / && !/127\.0\.0\.1/ {print $2; exit}' | cut -d/ -f1
    fi
}

get_access_url() {
    local public_ip=$(get_public_ip); local lan_ip=$(get_lan_ip)
    [[ -n "$public_ip" ]] && echo "http://${public_ip}:${EXTERNAL_PORT}" || { [[ -n "$lan_ip" ]] && echo "http://${lan_ip}:${EXTERNAL_PORT} (内网)"; } || echo "http://<无法获取IP>:${EXTERNAL_PORT}"
}

check_deps() {
    local missing=()
    command -v python3 &>/dev/null || missing+=("python3")
    python3 -c "import uvicorn" 2>/dev/null || missing+=("uvicorn (pip install uvicorn)")
    [[ ${#missing[@]} -gt 0 ]] && { err "缺少依赖: ${missing[*]}"; exit 1; }
}

# ══════════════════════════════════════════════
# systemd (Linux)
# ══════════════════════════════════════════════

generate_systemd_unit() {
    cat <<EOF
[Unit]
Description=Harness-Flow Dashboard
After=network.target

[Service]
Type=simple
User=${SUDO_USER:-$USER}
WorkingDirectory=${PROJECT_ROOT}
Environment="PATH=${PATH}"
ExecStart=${PYTHON_BIN} -c "
import uvicorn
from sw_lib.web.app import create_app
uvicorn.run(create_app(), host='${DASHBOARD_HOST}', port=${DASHBOARD_PORT}, log_level='info')
"
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF
}

# ══════════════════════════════════════════════
# launchd (macOS)
# ══════════════════════════════════════════════

generate_launchd_plist() {
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>-c</string>
        <string>
import uvicorn
from sw_lib.web.app import create_app
uvicorn.run(create_app(), host='${DASHBOARD_HOST}', port=${DASHBOARD_PORT}, log_level='info')
        </string>
    </array>

    <key>WorkingDirectory</key>
    <string>${PROJECT_ROOT}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>5</integer>

    <key>StandardOutPath</key>
    <string>${LOG_FILE}</string>

    <key>StandardErrorPath</key>
    <string>${LOG_FILE}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${PATH}</string>
    </dict>
</dict>
</plist>
EOF
}

# ══════════════════════════════════════════════
# install / uninstall
# ══════════════════════════════════════════════

cmd_install() {
    if has_systemd; then
        _install_systemd
    elif has_launchd; then
        _install_launchd
    else
        err "当前系统 ($UNAME) 不支持 systemd 或 launchd。"
        echo "  回退方案: 使用 'bash $0 start' 以 nohup 模式运行。"
        exit 1
    fi
}

_install_systemd() {
    if [[ -f "$SYSTEMD_FILE" ]]; then
        warn "服务文件已存在: $SYSTEMD_FILE"
        read -rp "是否覆盖? [y/N] " confirm
        [[ "$confirm" =~ ^[yY]$ ]] || exit 0
    fi
    log "生成 systemd 服务: $SYSTEMD_FILE"
    generate_systemd_unit | sudo tee "$SYSTEMD_FILE" > /dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl start "$SERVICE_NAME"
    sleep 2
    cmd_status
    echo ""
    log "安装完成！常用命令: bash $0 {status|logs|restart|stop|uninstall}"
    _start_tunnel_if_available
}

_install_launchd() {
    if [[ -f "$LAUNCHD_FILE" ]]; then
        warn "服务文件已存在: $LAUNCHD_FILE"
        read -rp "是否覆盖? [y/N] " confirm
        [[ "$confirm" =~ ^[yY]$ ]] || exit 0
    fi
    mkdir -p "$(dirname "$LAUNCHD_FILE")"
    log "生成 launchd plist: $LAUNCHD_FILE"
    generate_launchd_plist > "$LAUNCHD_FILE"
    _unload_launchd 2>/dev/null || true
    launchctl load "$LAUNCHD_FILE"
    sleep 2
    cmd_status
    echo ""
    log "安装完成！常用命令: bash $0 {status|logs|restart|stop|uninstall}"
    echo "  Dashboard 将在系统重启后自动启动 (RunAtLoad)。"
    _start_tunnel_if_available
}

_start_tunnel_if_available() {
    if command -v cloudflared &>/dev/null; then
        log "检测到 cloudflared，启动公网隧道..."
        start_cloudflared
        monitor_tunnel &
        disown -a 2>/dev/null || true
    else
        warn "未检测到 cloudflared，跳过公网隧道。"
        echo "  安装: brew install cloudflared"
        echo "  然后: bash $0 tunnel"
        echo ""
        echo "  局域网访问: $(get_access_url)"
    fi
}

cmd_uninstall() {
    if has_systemd; then
        [[ -f "$SYSTEMD_FILE" ]] || { warn "服务未安装"; return 0; }
        sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
        sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
        sudo rm -f "$SYSTEMD_FILE"
        sudo systemctl daemon-reload
    elif has_launchd; then
        [[ -f "$LAUNCHD_FILE" ]] || { warn "服务未安装"; return 0; }
        _unload_launchd
        rm -f "$LAUNCHD_FILE"
    else
        stop_all_nohup; return 0
    fi
    log "卸载完成"
}

_unload_launchd() {
    if launchctl list | grep -q "$PLIST_NAME"; then
        launchctl unload "$LAUNCHD_FILE" 2>/dev/null || true
    fi
}

# ══════════════════════════════════════════════
# start / stop / restart / status
# ══════════════════════════════════════════════

_has_service_file() {
    [[ -f "$SYSTEMD_FILE" ]] || [[ -f "$LAUNCHD_FILE" ]]
}

cmd_start() {
    check_deps
    if has_systemd && [[ -f "$SYSTEMD_FILE" ]]; then
        if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
            warn "Dashboard 已在运行中"; cmd_status; return 0
        fi
        sudo systemctl start "$SERVICE_NAME"
        sleep 2
        _start_tunnel_if_available
        cmd_status
    elif has_launchd && [[ -f "$LAUNCHD_FILE" ]]; then
        if launchctl list | grep -q "$PLIST_NAME"; then
            warn "Dashboard 已在运行中"; cmd_status; return 0
        fi
        launchctl load "$LAUNCHD_FILE"
        sleep 2
        _start_tunnel_if_available
        cmd_status
    else
        if _has_service_file; then true; else
            warn "守护进程未安装，回退到 nohup 模式。"
            echo "  推荐: 先运行 'bash $0 install' 安装为守护进程。"
            echo ""
        fi
        start_dashboard_nohup
        start_caddy
        start_cloudflared
        monitor_tunnel &
        echo ""; show_status_nohup
        echo ""; log "使用 'bash $0 stop' 停止服务"; disown -a 2>/dev/null || true
    fi
}

cmd_stop() {
    if has_systemd && [[ -f "$SYSTEMD_FILE" ]]; then
        systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null || { warn "Dashboard 未运行"; return 0; }
        sudo systemctl stop "$SERVICE_NAME"
        log "Dashboard 已停止"
    elif has_launchd && [[ -f "$LAUNCHD_FILE" ]]; then
        launchctl list | grep -q "$PLIST_NAME" || { warn "Dashboard 未运行"; return 0; }
        launchctl unload "$LAUNCHD_FILE"
        log "Dashboard 已停止"
    else
        stop_all_nohup
    fi
}

cmd_restart() {
    if has_systemd && [[ -f "$SYSTEMD_FILE" ]]; then
        sudo systemctl restart "$SERVICE_NAME"
        sleep 2; cmd_status
    elif has_launchd && [[ -f "$LAUNCHD_FILE" ]]; then
        launchctl unload "$LAUNCHD_FILE" 2>/dev/null || true
        sleep 1
        launchctl load "$LAUNCHD_FILE"
        sleep 2; cmd_status
    else
        stop_all_nohup; sleep 1
        cmd_start
    fi
}

_show_tunnel_status() {
    if [[ -f "$CF_URL_FILE" ]]; then
        local url=$(cat "$CF_URL_FILE")
        if [[ -f "$CF_PID_FILE" ]] && kill -0 "$(cat "$CF_PID_FILE")" 2>/dev/null; then
            echo -e "  外网:       ${CYAN}${url}${NC}"
        else
            echo -e "  外网:       ${YELLOW}${url} (隧道进程已退出)${NC}"
        fi
    fi
}

cmd_status() {
    if has_systemd && [[ -f "$SYSTEMD_FILE" ]]; then
        echo "═══════════════════════════════════════════"
        echo "  Harness-Flow Dashboard (systemd)"
        echo "═══════════════════════════════════════════"
        systemctl status "$SERVICE_NAME" --no-pager -l 2>/dev/null || {
            echo -e "  状态:       ${RED}服务未运行${NC}"
            echo "  启动:       bash $0 start"
        }
        _show_tunnel_status
    elif has_launchd && [[ -f "$LAUNCHD_FILE" ]]; then
        echo "═══════════════════════════════════════════"
        echo "  Harness-Flow Dashboard (launchd)"
        echo "═══════════════════════════════════════════"
        if launchctl list | grep -q "$PLIST_NAME"; then
            local pid=$(launchctl list | grep "$PLIST_NAME" | awk '{print $1}')
            echo -e "  状态:       ${GREEN}运行中${NC} (PID: ${pid:-N/A})"
            echo "  标签:       $PLIST_NAME"
            echo "  自启:       是 (RunAtLoad)"
            echo "  崩溃重启:   是 (KeepAlive)"
            echo "  日志:       $LOG_FILE"
            echo "  内网:       $(get_access_url)"
        else
            echo -e "  状态:       ${RED}未运行${NC}"
            echo "  启动:       bash $0 start"
        fi
        _show_tunnel_status
        echo "═══════════════════════════════════════════"
    else
        show_status_nohup
    fi
}

cmd_logs() {
    if has_systemd && [[ -f "$SYSTEMD_FILE" ]]; then
        [[ -t 0 ]] && log "实时日志 (Ctrl+C 退出)..." || true
        sudo journalctl -u "$SERVICE_NAME" $([[ -t 0 ]] && echo "-f") --no-pager
    elif has_launchd && [[ -f "$LAUNCHD_FILE" ]]; then
        [[ -t 0 ]] && log "实时日志 (Ctrl+C 退出)..." || true
        [[ -t 0 ]] && tail -f "$LOG_FILE" || tail -n 100 "$LOG_FILE"
    else
        [[ -f "$LOG_FILE" ]] && { [[ -t 0 ]] && tail -f "$LOG_FILE" || tail -n 100 "$LOG_FILE"; } || warn "暂无日志"
    fi
}

# ══════════════════════════════════════════════
# nohup 回退 (无 systemd/launchd 服务文件时)
# ══════════════════════════════════════════════

start_dashboard_nohup() {
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        warn "Dashboard 已在运行 (PID: $(cat "$PID_FILE"))"; return 0
    fi
    log "启动 Dashboard (${DASHBOARD_HOST}:${DASHBOARD_PORT})..."
    cd "$PROJECT_ROOT"
    nohup "$PYTHON_BIN" -c "
import uvicorn
from sw_lib.web.app import create_app
uvicorn.run(create_app(), host='${DASHBOARD_HOST}', port=${DASHBOARD_PORT}, log_level='info')
" > "$LOG_FILE" 2>&1 &
    local pid=$!; echo "$pid" > "$PID_FILE"
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        log "Dashboard 已启动 (PID: $pid)"
    else
        err "Dashboard 启动失败，查看日志: $LOG_FILE"; rm -f "$PID_FILE"; exit 1
    fi
}

start_caddy() {
    [[ -f "$CADDY_PID_FILE" ]] && kill -0 "$(cat "$CADDY_PID_FILE")" 2>/dev/null && { warn "Caddy 已在运行"; return 0; }
    [[ ! -f "$CADDYFILE" ]] && { err "Caddyfile 不存在: $CADDYFILE"; exit 1; }
    log "启动 Caddy 反向代理..."
    caddy run --config "$CADDYFILE" --adapter caddyfile &
    echo "$!" > "$CADDY_PID_FILE"
    sleep 1
    kill -0 "$(cat "$CADDY_PID_FILE")" 2>/dev/null || { err "Caddy 启动失败"; rm -f "$CADDY_PID_FILE"; exit 1; }
    log "Caddy 已启动"
}

check_tunnel_healthy() {
    local url=""; [[ -f "$CF_URL_FILE" ]] && url=$(cat "$CF_URL_FILE")
    [[ -z "$url" ]] && return 1
    # 只做轻量 HTTP 连通性检查（不依赖 DNS 解析和 curl 下载完整响应）
    curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 10 "$url" 2>/dev/null | grep -qE '^(2|3|4)' && return 0
    return 1
}

start_cloudflared() {
    local cf_url=""
    # 如果已有健康运行的隧道，直接复用
    if [[ -f "$CF_PID_FILE" ]] && kill -0 "$(cat "$CF_PID_FILE")" 2>/dev/null; then
        if check_tunnel_healthy; then
            cf_url=$(cat "$CF_URL_FILE" 2>/dev/null || true)
            [[ -n "$cf_url" ]] && { warn "Cloudflare Tunnel 已在运行"; return 0; }
        fi
    fi

    # 检测旧进程是否残留（例如僵尸进程），如有则清理
    if [[ -f "$CF_PID_FILE" ]]; then
        local old_pid=$(cat "$CF_PID_FILE")
        kill -0 "$old_pid" 2>/dev/null && kill "$old_pid" 2>/dev/null || true
        rm -f "$CF_PID_FILE"
        sleep 1
    fi

    log "启动 Cloudflare Tunnel..."
    rm -f "$CF_URL_FILE"

    # 使用 --retries 让 cloudflared 在网络抖动时自动重连而非直接退出
    cloudflared tunnel --protocol http2 --retries 5 \
        --url "http://${DASHBOARD_HOST}:${DASHBOARD_PORT}" \
        > /tmp/sw-cloudflared.log 2>&1 &
    local pid=$!; echo "$pid" > "$CF_PID_FILE"

    # 等待 URL 出现（最多 45s，比之前更宽容）
    for _ in $(seq 1 45); do
        sleep 1
        if grep -q 'trycloudflare\.com' /tmp/sw-cloudflared.log 2>/dev/null; then
            cf_url=$(grep -oE 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' /tmp/sw-cloudflared.log | head -1)
            if [[ -n "$cf_url" ]]; then
                echo "$cf_url" > "$CF_URL_FILE"
                log "外网地址: ${CYAN}${cf_url}${NC}"
                # 预热连接：发送一次请求确保隧道链路畅通
                curl -s -o /dev/null --connect-timeout 5 --max-time 10 "$cf_url" 2>/dev/null || true
                return 0
            fi
        fi
    done

    if kill -0 "$pid" 2>/dev/null; then
        warn "Tunnel 运行中但 45s 内未获取到 URL，查看 /tmp/sw-cloudflared.log"
        return 1
    fi
    warn "Tunnel 启动失败"; rm -f "$CF_PID_FILE"; return 1
}

monitor_tunnel() {
    echo $$ > "$CF_MONITOR_PID_FILE"
    local check_interval="${TUNNEL_CHECK_INTERVAL:-60}"
    local max_retries=3           # 连续健康检查失败次数阈值
    local retry_delay=5           # 每次重试间隔（秒）
    local restart_backoff=60      # 重启后首次检查的等待时间
    local fail_count=0
    local last_url=""

    log "隧道健康监控已启动 (检查间隔 ${check_interval}s, 容忍 ${max_retries} 次连续失败)"

    while true; do
        sleep "$check_interval"

        # 进程丢了 → 直接重建（进程死亡是确定性失败，无需重试）
        if [[ ! -f "$CF_PID_FILE" ]] || ! kill -0 "$(cat "$CF_PID_FILE")" 2>/dev/null; then
            warn "Tunnel 进程丢失，重建中..."
            fail_count=0
            start_cloudflared
            sleep "$restart_backoff"  # 给新隧道充分建立时间
            continue
        fi

        # 健康检查：单次失败不杀进程，连续失败才判定为真故障
        if check_tunnel_healthy; then
            fail_count=0
            last_url=$(cat "$CF_URL_FILE" 2>/dev/null || true)
            # 检查域名是否意外变更
            if [[ -n "$last_url" ]] && [[ -f "$CF_URL_FILE" ]]; then
                local cur_url=$(cat "$CF_URL_FILE" 2>/dev/null || true)
                if [[ -n "$cur_url" ]] && [[ "$cur_url" != "$last_url" ]]; then
                    warn "⚠️ 隧道域名意外变更: ${last_url} → ${cur_url}"
                    last_url="$cur_url"
                fi
            fi
            continue
        fi

        # 单次健康检查失败 → 快速重试几次（网络抖动容忍）
        fail_count=$((fail_count + 1))
        local recovered=0
        for i in $(seq 1 $((max_retries - 1))); do
            sleep "$retry_delay"
            if check_tunnel_healthy; then
                recovered=1
                break
            fi
        done

        if [[ "$recovered" -eq 1 ]]; then
            fail_count=0
            log "隧道从瞬态故障中恢复 (重试 ${i}/${max_retries})"
            continue
        fi

        # 连续多次失败 → 确认是真故障，重启隧道
        warn "隧道连续 ${max_retries} 次健康检查失败，重建隧道..."
        if [[ -f "$CF_PID_FILE" ]]; then
            kill "$(cat "$CF_PID_FILE")" 2>/dev/null || true
            rm -f "$CF_PID_FILE"
        fi
        # 保留 CF_URL_FILE 作为审计记录（不删除，新 start_cloudflared 会覆盖）
        fail_count=0
        start_cloudflared
        sleep "$restart_backoff"
    done
}

stop_all_nohup() {
    log "停止服务..."
    for f in "$PID_FILE" "$CADDY_PID_FILE" "$CF_MONITOR_PID_FILE" "$CF_PID_FILE"; do
        [[ -f "$f" ]] && { local p=$(cat "$f"); kill -0 "$p" 2>/dev/null && kill "$p" 2>/dev/null || true; rm -f "$f"; }
    done
    rm -f "$CF_URL_FILE"
    log "所有服务已停止"
}

show_status_nohup() {
    echo "═══════════════════════════════════════════"
    echo "  Harness-Flow Dashboard (nohup)"
    echo "═══════════════════════════════════════════"
    [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null \
        && echo -e "  Dashboard:  ${GREEN}运行中${NC} (PID: $(cat "$PID_FILE"))" \
        || echo -e "  Dashboard:  ${RED}未运行${NC}"
    [[ -f "$CF_URL_FILE" ]] && echo "  外网:       $(cat "$CF_URL_FILE")" \
        || echo "  局域网:     $(get_access_url)"
    echo "═══════════════════════════════════════════"
}

# ══════════════════════════════════════════════
# tunnel 子命令
# ══════════════════════════════════════════════

cmd_tunnel() {
    check_deps
    if _has_service_file; then
        cmd_start
        start_cloudflared
        monitor_tunnel &
        disown -a 2>/dev/null || true
    else
        start_dashboard_nohup
        start_cloudflared
        monitor_tunnel &
        echo ""; show_status_nohup
        echo ""; log "使用 'bash $0 stop' 停止服务"; disown -a 2>/dev/null || true
    fi
}

# ══════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════

usage() {
    echo "用法: bash $0 <command>"
    echo ""
    echo "命令:"
    echo "  install  - 安装为守护进程（Linux→systemd / macOS→launchd）"
    echo "  start    - 启动 Dashboard + Caddy + Cloudflare Tunnel"
    echo "  stop     - 停止所有服务"
    echo "  restart  - 重启服务"
    echo "  status   - 查看运行状态"
    echo "  logs     - 查看日志 (journalctl / tail -f)"
    echo "  uninstall- 停止并移除守护进程"
    echo "  tunnel   - 仅 Dashboard + Cloudflare Tunnel（无 Caddy）"
    echo ""
    if has_systemd; then
        echo "当前: Linux (systemd)"
    elif has_launchd; then
        echo "当前: macOS (launchd)"
    else
        echo "当前: $UNAME (nohup 回退)"
    fi
}

case "${1:-usage}" in
    install)    cmd_install ;;
    uninstall)  cmd_uninstall ;;
    start)      cmd_start ;;
    stop)       cmd_stop ;;
    restart)    cmd_restart ;;
    status)     cmd_status ;;
    logs)       cmd_logs ;;
    tunnel)     cmd_tunnel ;;
    usage|help|-h|--help) usage ;;
    *)          usage; exit 1 ;;
esac
