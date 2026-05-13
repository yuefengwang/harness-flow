#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'
info() { printf "${GREEN}[✓]${NC} %s\n" "$*"; }
error() { printf "${RED}[✗]${NC} %s\n" "$*"; }

declare -A AGENTS=(
    [opencode]="opencode-ai"
    [claude]="@anthropic-ai/claude-code"
    [gemini]="@google/gemini-cli"
)

AGENT="${1:-all}"

build_one() {
    local name="$1"
    local pkg="$2"
    local tag="sw-agent:${name}"
    info "构建镜像: ${tag} (${pkg})"
    docker build \
        --build-arg "AGENT_PKG=${pkg}" \
        -t "${tag}" \
        -f "${SCRIPT_DIR}/Dockerfile" \
        "${SCRIPT_DIR}" \
        && info "完成: ${tag}" \
        || error "失败: ${tag}"
}

if [[ "$AGENT" == "all" ]]; then
    for name in "${!AGENTS[@]}"; do
        build_one "$name" "${AGENTS[$name]}"
    done
elif [[ -n "${AGENTS[$AGENT]:-}" ]]; then
    build_one "$AGENT" "${AGENTS[$AGENT]}"
else
    echo "用法: $0 [opencode|claude|gemini|all]"
    exit 1
fi

echo ""
info "可用镜像:"
docker images sw-agent:* --format '  {{.Tag}}  {{.Size}}'
