#!/usr/bin/env bash
# aion one-line installer (macOS / Linux)
#
# 用法:
#   curl -fsSL https://<releases-url>/install.sh | bash
#
# 可选环境变量:
#   AION_VERSION       - 指定版本 (默认 latest)
#   AION_BIN_DIR       - 二进制安装目录 (默认 /usr/local/lib/aion，不可写时回退 ~/.local/lib/aion)
#   AION_CONFIG_DIR    - 配置/数据目录 (默认 ~/.aion)
#   AION_RELEASES_URL  - 发布包下载基地址
set -euo pipefail

# --- 配置 ---
: "${AION_VERSION:=latest}"
: "${AION_CONFIG_DIR:=$HOME/.aion}"
: "${AION_RELEASES_URL:=https://example.com/releases}"
PLIST_LABEL="com.user.aion.gateway"
PLIST_NAME="${PLIST_LABEL}.plist"

# --- 辅助函数 ---
echo_success() { printf '\033[0;32m==> \033[0m%s\n' "$*"; }
echo_info()    { printf '\033[0;36m==> \033[0m%s\n' "$*"; }
echo_warn()    { printf '\033[0;33mWarning: \033[0m%s\n' "$*" >&2; }
echo_error()   { printf '\033[0;31mERROR:   \033[0m%s\n' "$*" >&2; }

# --- 平台检测 ---
detect_platform() {
    local os arch
    case "$(uname -s)" in
        Darwin) os="darwin" ;;
        Linux)  os="linux" ;;
        *)      echo_error "Unsupported OS: $(uname -s)"; exit 1 ;;
    esac
    case "$(uname -m)" in
        x86_64|amd64)  arch="amd64" ;;
        aarch64|arm64) arch="arm64" ;;
        *)             echo_error "Unsupported arch: $(uname -m)"; exit 1 ;;
    esac
    echo "${os}-${arch}"
}

PLATFORM=$(detect_platform)

# --- 定位二进制源和 plist ---
SCRIPT_NAME="$0"
SCRIPT_DIR=$(CDPATH="" cd -- "$(dirname "$0")" && pwd 2>/dev/null || pwd)

find_binary_dir() {
    # PyInstaller 产物目录（内含 aion 可执行文件 + _internal）
    [ -d "$1/aion" ] && echo "$1/aion" && return
    # 仓库内 dev 路径
    [ -d "$1/../dist/aion" ] && echo "$(cd "$1/../dist/aion" && pwd)" && return
    echo ""
}

find_plist() {
    [ -f "$1/${PLIST_NAME}" ] && echo "$1/${PLIST_NAME}" && return
    [ -f "$1/../scripts/${PLIST_NAME}" ] && echo "$(cd "$1/../scripts" && pwd)/${PLIST_NAME}" && return
    echo ""
}

AION_SRC=$(find_binary_dir "$SCRIPT_DIR")
PLIST_SRC=$(find_plist "$SCRIPT_DIR")

# --- 远程模式：下载发布包 ---
if [ -z "$AION_SRC" ]; then
    TMPDIR=$(mktemp -d)
    trap 'rm -rf "$TMPDIR"' EXIT

    if [ "$AION_VERSION" = "latest" ]; then
        echo_info "Fetching latest version..."
        LATEST_URL="${AION_RELEASES_URL%/}/latest"
        EFFECTIVE_URL=$(curl -fsSL -o /dev/null -w '%{url_effective}' "$LATEST_URL" 2>/dev/null || echo "")
        if [ -n "$EFFECTIVE_URL" ] && [ "$EFFECTIVE_URL" != "$LATEST_URL" ]; then
            AION_VERSION=$(basename "$EFFECTIVE_URL" | sed 's/^v//')
        fi
        if [ "$AION_VERSION" = "latest" ]; then
            echo_error "Unable to determine latest version. Set AION_VERSION explicitly."
            exit 1
        fi
    fi

    TARBALL="aion-${AION_VERSION}-${PLATFORM}.tar.gz"
    DOWNLOAD_URL="${AION_RELEASES_URL%/}/download/v${AION_VERSION}/${TARBALL}"

    echo_info "Downloading aion ${AION_VERSION} for ${PLATFORM}..."
    echo_info "  ${DOWNLOAD_URL}"

    curl -fsSL --progress-bar -o "${TMPDIR}/${TARBALL}" "$DOWNLOAD_URL" || {
        echo_error "Download failed"
        echo_error "Make sure the release exists: ${DOWNLOAD_URL}"
        echo_error "Or override AION_RELEASES_URL for self-hosted releases."
        exit 1
    }

    echo_info "Extracting..."
    tar xzf "${TMPDIR}/${TARBALL}" -C "$TMPDIR"

    EXTRACTED_DIR=$(find "$TMPDIR" -maxdepth 2 -type d -name "aion*" ! -name "*.gz" | head -1)
    [ -n "$EXTRACTED_DIR" ] || { echo_error "Failed to locate extracted directory"; exit 1; }

    AION_SRC=$(find_binary_dir "$EXTRACTED_DIR")
    PLIST_SRC=$(find_plist "$EXTRACTED_DIR")
    [ -n "$AION_SRC" ] || { echo_error "Binary not found in archive: ${TARBALL}"; exit 1; }
fi

# --- 确定安装路径 ---
# 统一判断：能否使用系统路径（/usr/local），不能则回退到 ~/.local
determine_install_paths() {
    if [ -n "${AION_BIN_DIR:-}" ]; then
        AION_LIB_DIR="$AION_BIN_DIR"
    elif [ -w /usr/local/lib ] || [ -w /usr/local/bin ]; then
        # 当前用户可直接写入系统路径
        AION_LIB_DIR="/usr/local/lib/aion"
    elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        # 有无密码 sudo
        AION_LIB_DIR="/usr/local/lib/aion"
    else
        AION_LIB_DIR="${HOME}/.local/lib/aion"
    fi

    if [ "$AION_LIB_DIR" = "/usr/local/lib/aion" ]; then
        AION_SYMLINK="/usr/local/bin/aion"
    else
        AION_SYMLINK="${HOME}/.local/bin/aion"
    fi
}

determine_install_paths

# --- 安装二进制 ---
echo_success "Installing aion binary to ${AION_LIB_DIR}..."

if [ -d "$AION_LIB_DIR" ]; then
    rm -rf "$AION_LIB_DIR"
fi
mkdir -p "$AION_LIB_DIR"

if command -v rsync >/dev/null 2>&1; then
    rsync -a "${AION_SRC}/" "${AION_LIB_DIR}/"
else
    cp -R "${AION_SRC}/"* "${AION_LIB_DIR}/"
fi

BIN_PATH="${AION_LIB_DIR}/aion"

# --- 初始化配置目录 ---
CONFIG_DIR="$AION_CONFIG_DIR"
mkdir -p "$CONFIG_DIR"

if [ ! -f "${CONFIG_DIR}/aion.json" ]; then
    echo_info "First install, running aion setup..."
    "$BIN_PATH" setup || echo_warn "aion setup failed, you may need to configure manually"
else
    echo_info "Config file exists, skipping init"
fi

# --- 创建软链接 ---
SYMLINK_DIR=$(dirname "$AION_SYMLINK")
mkdir -p "$SYMLINK_DIR"

symlink_ok=0
if [ -w "$SYMLINK_DIR" ]; then
    ln -sf "$BIN_PATH" "$AION_SYMLINK" && symlink_ok=1
elif command -v sudo >/dev/null 2>&1; then
    sudo ln -sf "$BIN_PATH" "$AION_SYMLINK" 2>/dev/null && symlink_ok=1 || true
fi

if [ "$symlink_ok" = 0 ]; then
    echo_warn "Cannot create symlink at ${AION_SYMLINK} (requires sudo)"
    echo_warn "Add to PATH manually: export PATH=\"${AION_LIB_DIR}:\$PATH\""
fi

# --- macOS LaunchAgent ---
if [ "$(uname)" = "Darwin" ] && [ -n "$PLIST_SRC" ]; then
    LAUNCH_DIR="${HOME}/Library/LaunchAgents"
    mkdir -p "$LAUNCH_DIR"
    PLIST_DST="${LAUNCH_DIR}/${PLIST_NAME}"

    sed -e "s|__BIN_DIR__|${AION_LIB_DIR}|g" \
        -e "s|__CONFIG_DIR__|${CONFIG_DIR}|g" \
        -e "s|__HOME__|${HOME}|g" \
        "$PLIST_SRC" > "$PLIST_DST"

    launchctl bootout "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || true
    # 确保旧进程完全退出再重新加载
    sleep 1
    if [ -f "${BIN_PATH}" ]; then
        "${BIN_PATH}" stop >/dev/null 2>&1 || true
    fi

    # aion stop 可能已删除 plist，重新创建再 bootstrap
    sed -e "s|__BIN_DIR__|${AION_LIB_DIR}|g" \
        -e "s|__CONFIG_DIR__|${CONFIG_DIR}|g" \
        -e "s|__HOME__|${HOME}|g" \
        "$PLIST_SRC" > "$PLIST_DST"

    if launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" 2>/dev/null; then
        launchctl enable "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || true
        echo_success "LaunchAgent installed (auto-start on boot enabled)"
        echo "  stop:       launchctl bootout gui/$(id -u)/${PLIST_LABEL}"
    else
        echo_warn "LaunchAgent registration skipped (non-critical)"
        echo_warn "  launchctl 可能不可用或服务已存在，不影响 aion 使用"
        echo_warn "  如需手动注册: launchctl bootstrap gui/$(id -u) $PLIST_DST"
    fi
fi

# --- Linux systemd ---
if [ "$(uname)" = "Linux" ]; then
    SYSTEMD_DIR="${HOME}/.config/systemd/user"
    mkdir -p "$SYSTEMD_DIR"
    SERVICE_FILE="${SYSTEMD_DIR}/aion-gateway.service"

    cat > "$SERVICE_FILE" << SYSTEMDEOF
[Unit]
Description=aion Gateway Service
After=network.target

[Service]
Type=simple
ExecStart=${BIN_PATH} run
Restart=on-failure
RestartSec=5
Environment=HOME=${HOME}

[Install]
WantedBy=default.target
SYSTEMDEOF

    systemctl --user daemon-reload
    systemctl --user enable aion-gateway.service
    systemctl --user start aion-gateway.service

    echo_success "systemd service installed (auto-start on boot enabled)"
    echo "  status:     systemctl --user status aion-gateway"
    echo "  stop:       systemctl --user stop aion-gateway"
fi

# --- 完成 ---
echo ""
echo_success "aion ${AION_VERSION} installation complete!"
echo "  Binary:     ${BIN_PATH}"
echo "  Symlink:    ${AION_SYMLINK}"
echo "  Config:     ${CONFIG_DIR}/aion.json"
echo "  Workspaces: ${CONFIG_DIR}/workspaces/"
echo ""
echo "  Chat:       aion chat \"你好\""
echo "  Help:       aion --help"
echo "  Uninstall:  aion uninstall"
