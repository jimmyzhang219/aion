#!/usr/bin/env bash
# aion 构建打包脚本
# 输出: dist/aion-<version>-<platform>.tar.gz
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Running lint check..."
ruff check src/ 2>&1 || {
    echo "FAIL: ruff 检测到问题，请修复后重新打包"
    exit 1
}

echo "==> Verifying tool module imports..."
# 确保 src/ 在 Python 路径上（开发环境可能未 pip install -e .）
PYTHONPATH="${ROOT}/src:${PYTHONPATH:-}" python3 -c "
import sys
sys.path.insert(0, '${ROOT}/src')
from aion.tools import _toolkit
for name in sorted(_toolkit.TOOL_REGISTRY):
    print(f'  ✅ {name}')
print(f'All {len(_toolkit.TOOL_REGISTRY)} tool modules imported OK')
" 2>&1 || { echo "FAIL: 工具模块导入验证未通过"; exit 1; }

VERSION="${VERSION:-$(python -c 'import tomllib;print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')}"
PLATFORM="$(python -c 'import sys,platform;print(f"{platform.system().lower()}-{platform.machine()}")')"
OUTPUT="dist/aion-${VERSION}-${PLATFORM}.tar.gz"

echo "==> Generating build info..."
GIT_HASH="$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
BUILD_TIME="$(date +%Y-%m-%dT%H:%M:%S%z)"
cat > src/aion/_build_info.py << EOF
# 构建时自动生成 — 用于验证部署版本
BUILD_GIT_HASH = "${GIT_HASH}"
BUILD_TIME = "${BUILD_TIME}"
EOF

echo "==> Cleaning old build..."
rm -rf build/ dist/

echo "==> Building with PyInstaller..."
pyinstaller aion.spec

echo "==> Creating distribution archive..."
# Bundle binary + install script + plist into a single archive
mkdir -p "dist/aion-${VERSION}-${PLATFORM}"
cp -R dist/aion "dist/aion-${VERSION}-${PLATFORM}/"
cp scripts/install.sh "dist/aion-${VERSION}-${PLATFORM}/"
cp scripts/com.user.aion.gateway.plist "dist/aion-${VERSION}-${PLATFORM}/"
cd dist
tar czf "$(basename "$OUTPUT")" "aion-${VERSION}-${PLATFORM}/"
rm -rf "aion-${VERSION}-${PLATFORM}/"
cd ..

echo "==> Done: ${OUTPUT}"
ls -lh "$OUTPUT"
