# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

依赖声明唯一源：pyproject.toml。

hiddenimports 说明（不是依赖声明）：
  PyInstaller 通过静态扫描源码中的 import 语句自动发现需要打包的模块。
  但某些包内部使用 importlib.import_module("字符串") 动态加载子模块，
  静态分析看不到它们，必须在 hiddenimports 中显式声明。

  对这类包推荐用 collect_submodules() 自动收集全部子模块，
  避免新增传递依赖时漏写。注意：collect_submodules 在打包时
  会 import 该包，确保它已安装在当前环境中。
"""
from PyInstaller.utils.hooks import collect_submodules


a = Analysis(
    ['entry.py'],
    pathex=[],
    binaries=[],
    datas=[
        # tiktoken cl100k_base 编码表（打包后位于 aion/llm/_encodings/ 下）
        ('src/aion/llm/_encodings/cl100k_base.tiktoken', 'aion/llm/_encodings'),
        # PEP 561 py.typed 标记（类型检查器识别 aion 有内联类型标注）
        ('src/aion/py.typed', 'aion'),
    ],
    hiddenimports=[
        # ════════ 自动收集（动态导入较多，手动维护易遗漏） ════════
        # ChromaDB: telemetry.product.posthog 等通过 importlib 加载
        *collect_submodules('chromadb'),

        # aion.tools.builtin: _toolkit.py 通过 importlib.import_module 动态发现工具，
        # PyInstaller 静态扫描看不到，必须显式收集全部子模块
        *collect_submodules('aion.tools.builtin'),

        # ════════ 显式声明（自动收集覆盖不到或会误伤） ════════
        # Feishu / Lark（collect_submodules 会扫出 1w+ 无用子模块，需手动指定）
        'lark_oapi', 'lark_oapi.ws', 'lark_oapi.event',
        # Pydantic: C 编译扩展 + v1 向后兼容动态加载
        'pydantic', 'pydantic.v1',

        # ── LangChain 生态 ──
        'langchain', 'langchain_core', 'langchain_openai',
        'langchain_chroma', 'langchain_deepseek', 'langchain_ollama',
        'langgraph', 'langgraph.prebuilt',
        # ── Observability ──
        'langfuse',
        # 构建信息（build.sh 自动生成）
        'aion._build_info',
        # cffi 后端 C 扩展（PyInstaller 有时遗漏）
        '_cffi_backend',

        # ── ASR（阿里云、百度、macOS 原生、Whisper 离线语音识别 + 音频采集） ──
        'nls',
        'websockets',
        'pyaudio',
        'pydub',
        'webrtcvad',
        'faster-whisper',
        # macOS 原生: pyobjc 框架（Speech / AVFoundation / Cocoa）
        'Speech',
        'AVFoundation',
        'Cocoa',
        'objc',

        # ── HTTP / async ──
        'aiohttp',

        # ── 编码 / 序列化 ──
        'yaml',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 语音/音频 — ASR 使用阿里云云端 API，不需要本地 ML 推理框架
        'torch', 'torchaudio', 'torchvision',
        'av',
        'librosa',
        'numba', 'llvmlite',
        # ML 框架 - aion 未使用
        'scipy',
        'sklearn',
        'mlx',
        # ChromaDB ONNX 嵌入 - aion 使用 Chroma 但不使用 ONNX 嵌入
        'onnxruntime',
        # ChromaDB 单元测试 - aion 生产环境不需要
        'chromadb.test',
        # ChromaDB telemetry - aion 不需要，但 opentelemetry 已作为传递依赖安装
        # 排除 telemetry 会导致 chromadb 内部导入失败，写入静默失效
        # 只排除需要 structlog 的 posthog（structlog 未安装）
        'chromadb.telemetry.product.posthog',
        # SQLAlchemy 可选后端 - aion 只用 sqlite，排除 pysqlite2/MySQLdb
        'pysqlite2', 'MySQLdb',
        # pycparser 运行时生成的文件（lextab/yacctab 是 PLY 生成的表，不存在于当前环境）
        'pycparser.lextab', 'pycparser.yacctab',
        # importlib_resources 向后兼容回退（当前环境不需要）
        'importlib_resources.trees',
        # 浏览器自动化 - aion 未使用
        'playwright',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='aion',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='aion',
)

# ── macOS .app bundle（仅 macOS，Linux/Windows 跳过） ──
import platform as _platform
if _platform.system() == 'Darwin':
    app = BUNDLE(
          coll,
          name='Aion.app',
          bundle_identifier='com.aion.asr',
          entitlements_file='/tmp/aion.entitlements',
          codesign_identity='Apple Development: aaron3323@msn.com (FD69QPT6WR)',
          info_plist={
              'NSSpeechRecognitionUsageDescription': 'Aion 需要语音识别权限来实现会议实时转写。',
              'NSMicrophoneUsageDescription': 'Aion 需要麦克风权限来采集会议音频。',
              'LSBackgroundOnly': False,
          },
    )
