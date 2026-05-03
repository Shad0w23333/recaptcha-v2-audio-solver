import json
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

// 5. 阻止常见的 cdc_ 全局变量泄露(undetected-chromedriver 已经处理大部分,这里兜底)
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
    # selenium driver.quit() 在 Chrome 看来等同于"被 kill",会把 profile 的
    # exit_type 标记为 'Crashed'。下次启动时 Chrome 会进入会话恢复流程,
    # 恢复上次所有 tab,导致出现 3 个 window handle、demo 页面不是焦点、
    # iframe 不可见,solver 找不到/点不到复选框。
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

    # 清理会话文件(Chrome 可恢复的 tab 列表)
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
    # profile 复用时偶尔仍会出现多 tab (omnibox popup、内部 chrome:// 页面等),
    # 而且 driver 当前 window 不一定是"真正的"普通 tab,直接 close-all-but-current
    # 可能把唯一可用的 window 也关了。
    # 这里的策略是:开一个全新的空白 tab → 关掉所有旧 tab → 切到新 tab。
    # 这样无论旧 tab 是什么状态,最后剩下的一定是干净的 about:blank。
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
    # 不要使用用户日常 Chrome 的 profile 目录(会冲突且可能泄露 cookies),
    # 而是项目根目录下的 .chrome_profile/(已加入 .gitignore)。
    if custom_dir:
        path = os.path.abspath(custom_dir)
    else:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(project_root, ".chrome_profile")
    os.makedirs(path, exist_ok=True)
    return path


def create_driver(persistent_profile=True, user_data_dir=None):
    """创建配置好反检测的 Chrome 实例。

    参数:
        persistent_profile: 是否使用持久化 user profile。默认 True,
            让浏览器复用 cookies/history,显著降低被 reCAPTCHA 风控的概率。
            设为 False 时每次都是全新 profile (容易触发 'Try again later')。
        user_data_dir: 自定义 profile 目录。仅在 persistent_profile=True 时生效。
    """
    options = Options()
    # 不全屏 —— 给一个适中的窗口尺寸,既能完整显示 reCAPTCHA 与状态面板,
    # 又不会霸占整个屏幕。
    options.add_argument("--window-size=1100,820")

    # 强制 Chrome 使用英文界面 —— 否则 reCAPTCHA 会根据系统语言(例如 zh-CN)
    # 把 iframe 的 title、错误文案翻译成中文,导致 solver 中按英文文本写死的
    # XPath/字符串匹配 ('challenge'、'Try again later'、'multiple correct')
    # 全部失效。
    options.add_argument("--lang=en-US")

    # 反检测:阻止 Chrome 暴露 "受自动化控制" 的 navigator.webdriver 标志。
    # 这是最有效的单个反检测参数。
    options.add_argument("--disable-blink-features=AutomationControlled")

    # 复用 user-data-dir 时,如果上次 Chrome 异常退出 (selenium driver.quit
    # 可能让 Chrome 觉得自己"崩了"),下次启动会弹出 "会话恢复" 对话框,
    # 导致 driver 找不到主窗口、后续操作全部失败。下面这组参数禁掉所有
    # 非必要的首次运行 / 崩溃恢复 UI。
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-session-crashed-bubble")
    options.add_argument("--hide-crash-restore-bubble")
    options.add_argument("--disable-features=ChromeWhatsNewUI,InfiniteSessionRestore")

    # 自动对齐本机 Chrome 主版本,避免 ChromeDriver/Chrome 版本不匹配的崩溃
    version_main = _detect_chrome_major_version()

    # 持久化 user-data-dir。这是降低风控概率的核心策略。
    profile_dir = _resolve_user_data_dir(user_data_dir) if persistent_profile else None

    # 启动前修复 Crashed 标记,避免触发 Chrome 的会话恢复流程
    if profile_dir:
        _sanitize_profile_state(profile_dir)

    chrome_kwargs = {"options": options}
    if version_main:
        chrome_kwargs["version_main"] = version_main
    if profile_dir:
        chrome_kwargs["user_data_dir"] = profile_dir

    driver = uc.Chrome(**chrome_kwargs)

    # 关掉 Chrome 启动时遗留的多余窗口(omnibox popup、恢复的 tab 等),
    # 确保 driver 操作的是单个干净的 tab。注意必须先做这一步,因为
    # CDP 命令(Network/Page)是 per-target 的,新建 tab 后才在新 target
    # 上执行,前面注入的 stealth JS 才会作用于真正使用的 tab。
    _close_extra_windows(driver)

    # 通过 CDP 再保险一次:覆盖 Accept-Language 请求头 + 注入 stealth JS。
    # Network/Page domain 必须先 enable,否则相关命令会报错。
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

    # set_window_size 是冗余调用 (--window-size 启动参数已经设过了)。
    # 在某些情况下 (会话恢复对话框、profile 复用首启动较慢等) 会拿不到
    # 主窗口而抛 "Browser window not found",这里包 try/except 让它不致命。
    try:
        driver.set_window_size(1100, 820)
    except Exception:
        pass

    return driver
