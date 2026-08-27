"""飞书扫码绑定认证

设计文档: docs/design/feishu-channel.md 第 A 节

使用 OAuth 2.0 Device Authorization Grant 流程实现扫码绑定。
"""

import asyncio
import json
import time
import webbrowser
from dataclasses import dataclass
from typing import Optional, Literal

QRCODE_AVAILABLE = False  # qrcode + PIL 是否可用

try:
    import qrcode
    from PIL import Image  # noqa: F401

    QRCODE_AVAILABLE = True
except ImportError:
    pass


# OAuth 设备授权 API 端点
FEISHU_ACCOUNTS_URL = "https://accounts.feishu.cn"  # 国内飞书
LARK_ACCOUNTS_URL = "https://accounts.larksuite.com"  # 国际版 Lark
REGISTRATION_PATH = "/oauth/v1/app/registration"  # 注册接口路径

# HTTP 请求超时（毫秒）
REQUEST_TIMEOUT_MS = 10_000


@dataclass
class AuthResult:
    """OAuth 设备授权流程成功后的认证结果

    包含应用凭证与授权用户标识，可写入 aion 配置。
    """

    app_id: str  # 应用 App ID
    app_secret: str  # 应用 App Secret
    domain: str  # feishu 或 lark
    open_id: Optional[str] = None  # 授权用户 open_id


@dataclass
class BeginResult:
    """设备授权流程「开始注册」阶段的返回结果

    用户需扫码 qr_url 或输入 user_code，随后用 device_code 轮询授权状态。
    """

    device_code: str  # 设备码，用于轮询
    qr_url: str  # 扫码 URL
    user_code: str  # 用户可读授权码
    interval: int  # 轮询间隔（秒）
    expire_in: int  # 过期时间（秒）


class FeishuAuth:
    """飞书扫码认证

    使用 OAuth 2.0 Device Authorization Grant 流程。
    """

    def __init__(self, domain: Literal["feishu", "lark"] = "feishu"):
        """初始化认证客户端

        Args:
            domain: 飞书域名，"feishu" 为国内，"lark" 为国际版
        """
        self.domain = domain
        self.base_url = LARK_ACCOUNTS_URL if domain == "lark" else FEISHU_ACCOUNTS_URL

    def _accounts_url(self) -> str:
        """构建 OAuth 注册 API 完整 URL

        Returns:
            str: 注册端点 URL
        """
        return f"{self.base_url}{REGISTRATION_PATH}"

    async def _post(self, data: dict) -> dict:
        """向飞书 OAuth 端点发送 POST 请求

        Args:
            data: 表单字段字典

        Returns:
            dict: 解析后的 JSON 响应；HTTP 错误时仍尝试解析响应体
        """
        import urllib.request
        import urllib.parse
        import urllib.error

        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(
            self._accounts_url(),
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_MS / 1000) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            # OAuth 会返回 400 等错误码，但响应体包含有用信息
            try:
                return json.loads(e.read().decode())
            except:
                return {"error": f"HTTP {e.code}", "error_description": str(e)}
        except Exception as e:
            return {"error": str(e)}

    async def init_registration(self) -> dict:
        """步骤1: 初始化注册

        验证环境支持 client_secret 认证。

        Returns:
            dict 包含 supported_auth_methods 和 nonce
        """
        return await self._post({"action": "init"})

    async def begin_registration(self, nonce: Optional[str] = None) -> BeginResult:
        """步骤2: 开始注册，获取设备码和二维码 URL

        Args:
            nonce: 可选，从 init_registration 获取的 nonce

        Returns:
            BeginResult 包含 device_code, qr_url, user_code 等
        """
        data = {
            "action": "begin",
            "archetype": "PersonalAgent",
            "auth_method": "client_secret",
            "request_user_info": "open_id",
        }
        if nonce:
            data["nonce"] = nonce

        result = await self._post(data)

        # 构建 QR URL
        qr_url = result.get("verification_uri_complete", "")
        if "from" not in qr_url:
            qr_url += "&from=oc_onboard&tp=ob_cli_app"

        return BeginResult(
            device_code=result.get("device_code", ""),
            qr_url=qr_url,
            user_code=result.get("user_code", ""),
            interval=result.get("interval", 5),
            expire_in=result.get("expire_in", 600),
        )

    async def poll_registration(
        self,
        device_code: str,
        interval: int,
        expire_in: int,
        tp: str = "ob_app",
    ) -> dict:
        """步骤3: 轮询等待授权

        Args:
            device_code: 设备码
            interval: 轮询间隔（秒）
            expire_in: 过期时间（秒）
            tp: 注册类型 (ob_user=用户模式, ob_app=应用模式)

        Returns:
            dict 包含认证结果或错误
        """
        deadline = time.time() + expire_in

        while time.time() < deadline:
            result = await self._post(
                {
                    "action": "poll",
                    "device_code": device_code,
                    "tp": tp,
                }
            )

            # OAuth 标准错误码：pending 表示用户尚未扫码，需继续轮询
            error = result.get("error")
            if error:
                if error == "authorization_pending":
                    await asyncio.sleep(interval)
                    continue
                elif error == "slow_down":
                    # 轮询过快，增大间隔后重试
                    interval += 5
                    await asyncio.sleep(interval)
                    continue
                elif error == "access_denied":
                    return {"status": "denied"}
                elif error == "expired_token":
                    return {"status": "expired"}
                else:
                    return {"status": "error", "message": error}

            # 轮询成功：响应含 client_id / client_secret
            if result.get("client_id") and result.get("client_secret"):
                # 根据 tenant_brand 自动切换 lark 域名
                domain = self.domain
                if result.get("user_info", {}).get("tenant_brand") == "lark":
                    domain = "lark"

                return {
                    "status": "success",
                    "app_id": result["client_id"],
                    "app_secret": result["client_secret"],
                    "domain": domain,
                    "open_id": result.get("user_info", {}).get("open_id"),
                }

            await asyncio.sleep(interval)

        return {"status": "timeout"}

    @staticmethod
    def print_qrcode(url: str) -> None:
        """显示二维码（生成 PNG 并在 macOS 上用系统默认应用打开）

        若未安装 qrcode/PIL，则仅打印 URL 供手动扫码。

        Args:
            url: 二维码 URL

        Returns:
            None
        """
        import tempfile

        if not QRCODE_AVAILABLE:
            print("请用飞书扫码授权: " + url)
            return

        try:
            # from PIL import Image  # 未使用（qr.make_image() 返回对象直接用）

            # 生成 QR 码图片
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_H,
                box_size=10,
                border=2,
            )
            qr.add_data(url)
            qr.make(fit=True)

            img = qr.make_image().convert("RGB")

            # 保存到临时文件
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                temp_path = f.name
                img.save(temp_path)

            # 跨平台打开图片
            webbrowser.open(temp_path)
            print("已在图片应用中打开二维码")
            print(f"URL: {url}")

            # 清理临时文件（延迟删除，等图片应用加载完）
            # 注意：实际应用中可以不删除，让系统清理
        except Exception:
            print("请用飞书扫码授权: " + url)

    async def authenticate(self) -> Optional[AuthResult]:
        """完整的扫码认证流程

        Returns:
            AuthResult 成功时返回认证结果，否则返回 None
        """
        print(f"正在初始化飞书认证 ({self.domain})...")

        # 步骤1: 初始化
        init_result = await self.init_registration()
        if not init_result.get("supported_auth_methods"):
            print("认证初始化失败")
            return None

        # 获取 nonce（用于后续请求）
        nonce = init_result.get("nonce")
        assert nonce is None or isinstance(nonce, str), f"nonce must be str or None, got {type(nonce)}"

        # 步骤2: 获取二维码
        print("正在获取授权信息...")
        begin_result = await self.begin_registration(nonce=nonce)

        # 打印二维码
        print("\n" + "=" * 50)
        print("请用飞书 App 扫码授权")
        print(f"授权码: {begin_result.user_code}")
        print("=" * 50 + "\n")
        self.print_qrcode(begin_result.qr_url)
        print()

        # 步骤3: 轮询等待授权
        print("等待授权...")
        poll_result = await self.poll_registration(
            device_code=begin_result.device_code,
            interval=begin_result.interval,
            expire_in=begin_result.expire_in,
        )

        if poll_result.get("status") == "success":
            print("\n授权成功!")
            return AuthResult(
                app_id=poll_result["app_id"],
                app_secret=poll_result["app_secret"],
                domain=poll_result.get("domain", self.domain),
                open_id=poll_result.get("open_id"),
            )
        elif poll_result.get("status") == "denied":
            print("\n授权被拒绝")
        elif poll_result.get("status") == "expired":
            print("\n授权已过期")
        elif poll_result.get("status") == "timeout":
            print("\n授权超时")
        else:
            print(f"\n授权失败: {poll_result.get('message')}")

        return None


async def main():
    """命令行入口：执行扫码绑定并打印凭证

    Returns:
        None
    """
    import argparse

    parser = argparse.ArgumentParser(description="飞书扫码绑定")
    parser.add_argument("--domain", choices=["feishu", "lark"], default="feishu", help="飞书域名")
    args = parser.parse_args()

    auth = FeishuAuth(domain=args.domain)
    result = await auth.authenticate()

    if result:
        print("\n--- 认证成功 ---")
        print(f"App ID: {result.app_id}")
        print(f"App Secret: {result.app_secret}")
        print(f"Domain: {result.domain}")
        if result.open_id:
            print(f"Open ID: {result.open_id}")
        print("\n请将以上信息添加到配置中")


if __name__ == "__main__":
    asyncio.run(main())
