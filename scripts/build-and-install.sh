#!/usr/bin/env bash
# 一键打包安装脚本 — 构建 → 解包 → 安装
# 相当于依次执行：
#   bash scripts/build.sh
#   进入 dist/ 解压 .tar.gz
#   进入解压目录执行 bash install.sh
set -euo pipefail

RED='\033[0;31m'
NC='\033[0m' # No Color
error_echo() {
  echo -e "${RED}ERROR:${NC} $*" >&2
  exit 1
}
trap 'error_echo "Script failed at line $LINENO"' ERR

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 使用 .venv 的 Python（项目约定）
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

# 1. 构建
echo "==> [1/3] Building distribution archive..."
bash scripts/build.sh

# 2. 定位 tar.gz
echo ""
echo "==> [2/3] Locating archive..."
OUTPUT="$(ls dist/aion-*.tar.gz | head -1)"
if [ -z "$OUTPUT" ]; then
  error_echo "No tar.gz found in dist/ after build"
fi
echo "  Found: $OUTPUT"

# 3. 解压并安装
echo ""
echo "==> [3/3] Extracting and installing..."
EXTRACT_DIR="$(mktemp -d)"
tar xzf "$OUTPUT" -C "$EXTRACT_DIR"
INSTALL_DIR="$(ls -d "$EXTRACT_DIR"/aion-*)"

pushd "$INSTALL_DIR" > /dev/null
bash install.sh
popd > /dev/null

rm -rf "$EXTRACT_DIR"
echo ""
echo "==> Done."
