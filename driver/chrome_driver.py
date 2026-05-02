import os
import re
import shutil
import subprocess
import sys

import undetected_chromedriver as uc
from selenium.webdriver.chrome.options import Options


def _detect_chrome_major_version():
    # 探测本机 Google Chrome 的主版本号 (例如 Chrome 147.x.y.z 返回 147)。
    # undetected-chromedriver 默认会去拉"最新"的 ChromeDriver,
    # 一旦本机 Chrome 不是最新版本就会出现 "only supports Chrome version N" 报错。
    # 这里显式探测后传给 uc.Chrome(version_main=...),保证版本对齐。
    candidates = []
    if sys.platform == "darwin":
        # macOS 默认安装路径
        candidates.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    elif sys.platform.startswith("linux"):
        # 常见 Linux 路径
        candidates += ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium"]
    elif sys.platform.startswith("win"):
        # Windows 默认安装路径
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))

    # 兜底:用 PATH 中的 google-chrome / chromium
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chrome"):
        path = shutil.which(name)
        if path:
            candidates.append(path)

    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            out = subprocess.check_output([path, "--version"], text=True, timeout=5)
            # 输出示例: "Google Chrome 147.0.7727.138"
            m = re.search(r"\b(\d+)\.", out)
            if m:
                return int(m.group(1))
        except Exception:
            continue
    return None


def create_driver():
    options = Options()
    options.add_argument("--window-size=1920,1080")

    # 强制 Chrome 使用英文界面 —— 否则 reCAPTCHA 会根据系统语言(例如 zh-CN)
    # 把 iframe 的 title、错误文案翻译成中文,导致 solver 中按英文文本写死的
    # XPath/字符串匹配 ('challenge'、'Try again later'、'multiple correct')
    # 全部失效。
    options.add_argument("--lang=en-US")

    # 自动对齐本机 Chrome 主版本,避免 ChromeDriver/Chrome 版本不匹配的崩溃
    version_main = _detect_chrome_major_version()
    if version_main:
        driver = uc.Chrome(options=options, version_main=version_main)
    else:
        driver = uc.Chrome(options=options)

    # 通过 CDP 再保险一次:覆盖 Accept-Language 请求头,
    # 避免 reCAPTCHA 根据 HTTP header 走中文。
    # 必须先 enable Network 域,否则 setExtraHTTPHeaders 会报错。
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd(
            "Network.setExtraHTTPHeaders",
            {"headers": {"Accept-Language": "en-US,en;q=0.9"}},
        )
    except Exception:
        pass

    driver.set_window_size(1920, 1080)
    try:
        driver.maximize_window()
    except Exception:
        pass

    return driver
