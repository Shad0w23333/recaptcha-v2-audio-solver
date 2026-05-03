import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


# Chrome 远程调试端口。固定 9222 是为了让多次运行的 Python 进程都连同一个
# Chrome 实例,避免反复 "启动 Chrome → 跑完 → 关 Chrome" 的开销。
# 如果与你机器上其他服务冲突,可以改这个常量。
REMOTE_DEBUG_PORT = 9222


def _detect_chrome_binary():
    # 找到本机 Chrome 的可执行路径。
    if sys.platform == "darwin":
        for p in ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]:
            if os.path.exists(p):
                return p
    elif sys.platform.startswith("linux"):
        for p in ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium"]:
            if os.path.exists(p):
                return p
    elif sys.platform.startswith("win"):
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                p = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
                if os.path.exists(p):
                    return p
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chrome"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _is_chrome_alive_on_debug_port():
    # 检测固定的 9222 端口上是否有 Chrome 在监听 DevTools Protocol。
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{REMOTE_DEBUG_PORT}/json/version")
        with urllib.request.urlopen(req, timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


# 在浏览器每个新文档加载时都会执行的"反指纹"JS。
# 用于覆盖 navigator.webdriver / plugins / languages / window.chrome 等
# 常被 Google reCAPTCHA 用来识别自动化浏览器的特征。
# 通过 CDP Page.addScriptToEvaluateOnNewDocument 注入,作用范围是所有 frame。
_STEALTH_JS = r"""
// 1. 隐藏 navigator.webdriver(最关键的自动化标志)
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 2. 让 navigator.plugins 看起来非空(headless / 自动化通常是 0)
Object.defineProperty(navigator, 'plugins', {
  get: () => [
    { name: 'Chrome PDF Plugin' },
    { name: 'Chrome PDF Viewer' },
    { name: 'Native Client' }
  ]
});

// 3. navigator.languages 与 Accept-Language 对齐
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

// 4. 真实 Chrome 的 window.chrome 是有内容的,自动化里通常空
if (!window.chrome) { window.chrome = {}; }
if (!window.chrome.runtime) { window.chrome.runtime = {}; }

// 5. 阻止常见的 cdc_ 全局变量泄露
for (var k in window) {
  if (k.indexOf('cdc_') === 0) {
    try { delete window[k]; } catch (e) {}
  }
}

// 6. Permissions API 一致性:reCAPTCHA 会查询 notifications 权限,
//    自动化浏览器经常返回 "denied" 但 navigator.permissions.query 报告 "default",造成不一致
if (navigator.permissions && navigator.permissions.query) {
  var origQuery = navigator.permissions.query.bind(navigator.permissions);
  navigator.permissions.query = function(p) {
    if (p && p.name === 'notifications') {
      return Promise.resolve({ state: Notification.permission, onchange: null });
    }
    return origQuery(p);
  };
}
"""


def _sanitize_profile_state(profile_dir):
    # 复用 user-data-dir 时,如果上次 Chrome 异常退出,Chrome 会进入会话恢复
    # 流程,恢复上次所有 tab,导致出现多 tab、demo 页面不是焦点等问题。
    # 这里在每次启动前:
    # 1) 把 exit_type / exited_cleanly 强制改回正常退出
    # 2) 删除 Sessions / Last Session / Last Tabs 等会话文件,让 Chrome
    #    根本没有可恢复的会话
    default_dir = os.path.join(profile_dir, "Default")

    pref_path = os.path.join(default_dir, "Preferences")
    if os.path.exists(pref_path):
        try:
            with open(pref_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            profile = data.setdefault("profile", {})
            changed = False
            if profile.get("exit_type") != "Normal":
                profile["exit_type"] = "Normal"
                changed = True
            if profile.get("exited_cleanly") is not True:
                profile["exited_cleanly"] = True
                changed = True
            if changed:
                with open(pref_path, "w", encoding="utf-8") as f:
                    json.dump(data, f)
        except Exception:
            pass

    for name in ("Sessions", "Session Storage", "Last Session", "Last Tabs",
                 "Current Session", "Current Tabs"):
        target = os.path.join(default_dir, name)
        if not os.path.exists(target):
            continue
        try:
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
            else:
                os.remove(target)
        except Exception:
            pass


def _close_extra_windows(driver):
    # profile 复用时可能出现多 tab。开一个全新空白 tab → 关掉所有旧 tab。
    try:
        old_handles = driver.window_handles
        if len(old_handles) <= 1:
            return
        driver.switch_to.new_window("tab")
        new_handle = driver.current_window_handle
        for h in old_handles:
            if h == new_handle:
                continue
            try:
                driver.switch_to.window(h)
                driver.close()
            except Exception:
                pass
        driver.switch_to.window(new_handle)
    except Exception:
        pass


def _resolve_user_data_dir(custom_dir):
    # 持久化的 Chrome user profile 目录:让浏览器看起来"用过",
    # 累积 cookies / 历史 / Google 信任度,避免 reCAPTCHA 把每次都当成新机器人。
    if custom_dir:
        path = os.path.abspath(custom_dir)
    else:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(project_root, ".chrome_profile")
    os.makedirs(path, exist_ok=True)
    return path


def _launch_detached_chrome(profile_dir):
    # 用 subprocess.Popen + start_new_session=True 启动 Chrome,
    # 让 Chrome 进程脱离当前 Python 进程组,Python 退出后 Chrome 不会被清理,
    # 这样下一次运行 Python 命令时可以直接 reconnect 到这个 Chrome,省启动时间。
    chrome_bin = _detect_chrome_binary()
    if not chrome_bin:
        print("[!] 未找到 Chrome 可执行文件,无法启动")
        return False

    args = [
        chrome_bin,
        f"--remote-debugging-port={REMOTE_DEBUG_PORT}",
        # window 大小由后续 driver.set_window_size 重新调整,这里给个初始值
        "--window-size=1100,820",
        # 强制英文(reCAPTCHA iframe title / 错误文本会跟系统语言走)
        "--lang=en-US",
        # 反检测核心
        "--disable-blink-features=AutomationControlled",
        # 禁掉所有非必要 UI
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "--disable-features=ChromeWhatsNewUI,InfiniteSessionRestore",
        # 启动加速
        "--disable-extensions",
        "--disable-component-extensions-with-background-pages",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-default-apps",
        "--disable-sync",
        "--disable-translate",
        "--disable-client-side-phishing-detection",
        "--disable-domain-reliability",
        "--metrics-recording-only",
    ]
    if profile_dir:
        args.append(f"--user-data-dir={profile_dir}")

    print(f"[i] 启动 detached Chrome (port {REMOTE_DEBUG_PORT}) ...")
    subprocess.Popen(
        args,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 等 DevTools 端口起来 (最多 10 秒)
    for _ in range(100):
        if _is_chrome_alive_on_debug_port():
            return True
        time.sleep(0.1)
    print(f"[!] Chrome 已启动但 {REMOTE_DEBUG_PORT} 端口在 10s 内仍未响应")
    return False


def create_driver(persistent_profile=True, user_data_dir=None):
    """创建/复用 Chrome 实例。

    工作流程:
    1) 检查端口 9222 是否已经有 Chrome 在跑(上次运行留下的)
       - 有:selenium 直接 connect,不重新启动 Chrome (~1s)
       - 没有:subprocess detach 启动 Chrome,再 connect (~5s)
    2) 应用 stealth JS / Network headers / 关多余 tab / 调整窗口

    参数:
        persistent_profile: 默认 True。使用 .chrome_profile/ 累积 Google 信任度。
        user_data_dir: 自定义 profile 目录。
    """
    profile_dir = _resolve_user_data_dir(user_data_dir) if persistent_profile else None

    # 如果 Chrome 已经活着 (上次运行留下的),直接复用。
    # 否则在此会先 _sanitize_profile_state + _launch_detached_chrome。
    if not _is_chrome_alive_on_debug_port():
        if profile_dir:
            _sanitize_profile_state(profile_dir)
        if not _launch_detached_chrome(profile_dir):
            raise RuntimeError(
                f"无法启动 detached Chrome。请检查端口 {REMOTE_DEBUG_PORT} 是否被占用。"
            )

    # selenium 通过 debuggerAddress 连接到现有 Chrome。
    # 这种模式下 driver.quit() 只断开 chromedriver session,Chrome 进程保持。
    options = Options()
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{REMOTE_DEBUG_PORT}")
    driver = webdriver.Chrome(options=options)

    # 关掉多余 tab,确保 driver 操作的是一个干净的 about:blank。
    # 顺序很重要:先开干净 tab、关多余,然后注入 CDP,因为 CDP 是 per-target 的。
    _close_extra_windows(driver)

    # CDP:Accept-Language header 强制英文 + stealth JS 注入
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd(
            "Network.setExtraHTTPHeaders",
            {"headers": {"Accept-Language": "en-US,en;q=0.9"}},
        )
    except Exception:
        pass
    try:
        driver.execute_cdp_cmd("Page.enable", {})
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": _STEALTH_JS},
        )
    except Exception:
        pass

    # 设置窗口尺寸:宽 1100,高度填满屏幕可用区域(竖向拉满)
    try:
        screen_h = driver.execute_script("return window.screen.availHeight")
        target_h = int(screen_h) if isinstance(screen_h, (int, float)) and screen_h > 600 else 820
        driver.set_window_size(1100, target_h)
        try:
            driver.set_window_position(0, 0)
        except Exception:
            pass
    except Exception:
        try:
            driver.set_window_size(1100, 820)
        except Exception:
            pass

    return driver
