import base64
import io
import os
import threading
import time
import traceback
from faster_whisper import WhisperModel
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# 在浏览器里 fetch audio 二进制并转 base64 返回。
# 关键:必须用浏览器发起请求 —— Python requests 会用 urllib3 的 TLS 指纹,
# 没有 reCAPTCHA session cookies、Referer、UA 也对不上,Google 立即把整个
# reCAPTCHA session 标记为可疑,下次 audio button 点击就回 'Try again later'。
# 用浏览器内 fetch 才能带上完整的 cookies / UA / TLS 指纹,与正常浏览器加载
# audio 元素的请求看起来一样。
_FETCH_AUDIO_JS = r"""
var url = arguments[0];
var done = arguments[arguments.length - 1];
fetch(url, {credentials: 'include', mode: 'cors'})
  .then(function(r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.arrayBuffer();
  })
  .then(function(buf) {
      var arr = new Uint8Array(buf);
      // 大文件分块 String.fromCharCode 避免栈溢出
      var bin = '';
      var chunk = 0x8000;
      for (var i = 0; i < arr.length; i += chunk) {
          bin += String.fromCharCode.apply(null, arr.subarray(i, i + chunk));
      }
      done({ok: true, b64: btoa(bin), size: arr.length});
  })
  .catch(function(e) { done({ok: false, error: String(e)}); });
"""


# 注入到浏览器顶层 window 的浮动状态面板,固定右上角。
# Python 端通过 driver.execute_script 调用 window.__captchaStatus(text, level)
# 与 window.__captchaSetAttempt(text) 来更新。
_PANEL_JS = r"""
(function() {
  var existing = document.getElementById('__captcha_solver_panel');
  if (existing) existing.remove();

  var panel = document.createElement('div');
  panel.id = '__captcha_solver_panel';
  // 注意:position/top/left 不在这里写死,由下面 positionPanel() 动态定位
  // 到 reCAPTCHA 复选框下方。
  panel.style.cssText = [
    'width:380px','max-height:70vh',
    'background:rgba(20,20,28,0.96)','color:#e0e6ed',
    'font-family:-apple-system,BlinkMacSystemFont,SF Mono,Monaco,Consolas,monospace',
    'font-size:13px','line-height:1.55','border-radius:12px',
    'box-shadow:0 8px 32px rgba(0,0,0,0.4)','z-index:2147483647',
    'overflow:hidden','backdrop-filter:blur(8px)',
    '-webkit-backdrop-filter:blur(8px)'
  ].join(';') + ';';

  var header = document.createElement('div');
  header.style.cssText = [
    'padding:10px 14px',
    'background:linear-gradient(135deg,#4a90e2 0%,#357abd 100%)',
    'color:white','font-weight:600','font-size:14px',
    'display:flex','justify-content:space-between','align-items:center'
  ].join(';') + ';';
  header.innerHTML = '<span>🤖 reCAPTCHA Solver</span>' +
    '<span id="__captcha_attempt" style="font-size:11px;opacity:0.9;font-weight:normal;padding:2px 8px;background:rgba(255,255,255,0.18);border-radius:10px;">就绪</span>';

  var body = document.createElement('div');
  body.id = '__captcha_log';
  body.style.cssText = [
    'padding:8px 0','max-height:calc(70vh - 50px)','overflow-y:auto'
  ].join(';') + ';';

  panel.appendChild(header);
  panel.appendChild(body);
  document.body.appendChild(panel);

  var COLORS = {info:'#a8b3c4',progress:'#7ec5ff',success:'#7ee787',
                warning:'#ffb454',error:'#ff6b6b'};
  var ICONS  = {info:'·',progress:'▸',success:'✓',warning:'⚠',error:'✕'};

  function escape(s){return String(s).replace(/[<>&]/g,function(c){
    return {'<':'&lt;','>':'&gt;','&':'&amp;'}[c];});}

  window.__captchaStatus = function(text, level) {
    var color = COLORS[level] || COLORS.info;
    var icon  = ICONS[level]  || ICONS.info;
    var line  = document.createElement('div');
    line.style.cssText = 'padding:3px 14px;color:' + color + ';word-break:break-all;';
    var t = new Date();
    var ts = String(t.getHours()).padStart(2,'0') + ':' +
             String(t.getMinutes()).padStart(2,'0') + ':' +
             String(t.getSeconds()).padStart(2,'0');
    line.innerHTML =
      '<span style="opacity:0.45;">' + ts + '</span> ' +
      '<span style="display:inline-block;width:14px;text-align:center;">' + icon + '</span>' +
      '<span>' + escape(text) + '</span>';
    var log = document.getElementById('__captcha_log');
    if (log) { log.appendChild(line); log.scrollTop = log.scrollHeight; }
  };

  window.__captchaSetAttempt = function(text) {
    var el = document.getElementById('__captcha_attempt');
    if (el) el.textContent = text;
  };

  // 找最下方那个可见的 reCAPTCHA iframe (anchor 或展开后的 challenge),
  // 把面板贴到它的正下方,留出 32px 间距,避免遮挡。
  // 当挑战 iframe 展开/收起时,面板会自动跟随。
  function findReCaptchaBottomIframe() {
    var all = document.querySelectorAll('iframe');
    var bottom = null;
    var maxY = -1;
    for (var i = 0; i < all.length; i++) {
      var f = all[i];
      var src = f.src || '';
      if (src.indexOf('recaptcha') === -1 &&
          (f.title || '').toLowerCase().indexOf('recaptcha') === -1) continue;
      var rect = f.getBoundingClientRect();
      if (rect.height === 0 || rect.width === 0) continue;
      if (rect.bottom > maxY) { maxY = rect.bottom; bottom = f; }
    }
    return bottom;
  }

  function positionPanel() {
    var iframe = findReCaptchaBottomIframe();
    if (iframe) {
      var rect = iframe.getBoundingClientRect();
      panel.style.position  = 'absolute';
      panel.style.top       = (rect.bottom + window.scrollY + 32) + 'px';
      panel.style.left      = (rect.left   + window.scrollX) + 'px';
      panel.style.right     = '';
      panel.style.transform = '';
    } else {
      panel.style.position  = 'fixed';
      panel.style.top       = '120px';
      panel.style.left      = '50%';
      panel.style.right     = '';
      panel.style.transform = 'translateX(-50%)';
    }
  }
  positionPanel();
  // 兜底:reCAPTCHA 可能稍后才完全渲染,200ms / 1000ms 再校正一次
  setTimeout(positionPanel, 200);
  setTimeout(positionPanel, 1000);
  // 周期校正:挑战 iframe 展开/收起时面板会自动跟到最下方 iframe 之下
  setInterval(positionPanel, 500);
  // 窗口尺寸变化时也重新定位
  window.addEventListener('resize', positionPanel);
  // 暴露给 Python 端按需触发(目前主流程暂未调用)
  window.__captchaRepositionPanel = positionPanel;

  window.__captchaStatus('面板已就绪', 'info');
})();
"""


class AudioCaptchaSolver:
    def __init__(self, driver, model_size="tiny", download_root=None):
        self.driver = driver
        self.model_size = model_size
        self.model = None

        if download_root is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.download_root = os.path.join(base_dir, "models")
        else:
            self.download_root = download_root
        os.makedirs(self.download_root, exist_ok=True)

    def _load_model(self):
        if self.model is None:
            self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8", download_root=self.download_root)

    def _start_model_preload(self):
        # 后台预加载 Whisper 模型,与 Chrome / reCAPTCHA 流程并行进行,
        # 等真正调用 transcribe 时模型已经 ready,省 ~1-2 秒。
        # 已经加载过则 _load_model 内部直接返回。
        threading.Thread(target=self._load_model, daemon=True).start()

    # ---------- 等待 helper(替代 time.sleep) ----------

    def _poll(self, predicate, timeout=5.0, interval=0.05):
        # 轮询 predicate(driver) 直到返回 truthy 或超时。
        # 比 time.sleep(N) 快几倍 —— sleep 是固定等 N 秒,
        # 这个一旦条件满足就立即返回,典型场景下 < 0.5 秒。
        # interval 0.05s ≈ 20Hz,响应延迟可忽略。
        end = time.time() + timeout
        while time.time() < end:
            try:
                if predicate(self.driver):
                    return True
            except Exception:
                pass
            time.sleep(interval)
        return False

    # ---------- 浏览器浮动面板 helpers ----------

    def _setup_panel(self):
        # 在顶层 window 注入面板。失败不影响主流程。
        try:
            self.driver.switch_to.default_content()
            self.driver.execute_script(_PANEL_JS)
        except Exception as e:
            print(f"[!] 注入面板失败 (不影响主流程): {e}")

    def _panel(self, text, level="info", restore_frame=None):
        # 任意 frame 上下文均可调用:先切回 default_content 调 JS 更新面板,
        # 再按 restore_frame 决定是否切回原 frame。restore_frame 无效时静默失败。
        try:
            self.driver.switch_to.default_content()
            self.driver.execute_script(
                "if(window.__captchaStatus)window.__captchaStatus(arguments[0],arguments[1]);",
                text, level,
            )
        except Exception:
            pass
        if restore_frame is not None:
            try:
                self.driver.switch_to.frame(restore_frame)
            except Exception:
                pass

    def _set_attempt(self, text, restore_frame=None):
        # 更新右上角的 attempt 计数标签
        try:
            self.driver.switch_to.default_content()
            self.driver.execute_script(
                "if(window.__captchaSetAttempt)window.__captchaSetAttempt(arguments[0]);",
                text,
            )
        except Exception:
            pass
        if restore_frame is not None:
            try:
                self.driver.switch_to.frame(restore_frame)
            except Exception:
                pass

    # ---------- 主流程 ----------

    def solve(self):
        self._setup_panel()
        self._set_attempt("启动")
        # 后台预加载 Whisper 模型,与下面的 reCAPTCHA 流程并行
        self._start_model_preload()
        try:
            print("[*] 寻找 reCAPTCHA 复选框 iframe...")
            self._panel("寻找 reCAPTCHA 复选框 iframe...", "progress")
            checkbox_frame = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//iframe[contains(@title, 'reCAPTCHA') and not(contains(@title, 'challenge'))]"))
            )
            self.driver.switch_to.frame(checkbox_frame)

            checkbox = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.ID, "recaptcha-anchor"))
            )
            checkbox.click()
            print("[*] 已点击复选框,等待 reCAPTCHA 响应...")
            self.driver.switch_to.default_content()
            self._panel("已点击复选框,等待响应...", "progress")

            # 替代固定 sleep(2):等到 challenge iframe 变可见 (要弹音频挑战)
            # 或 anchor 内 checkbox 变 checked (直接通过)。任一为真就立即继续。
            def _checkbox_response_ready(d):
                # 1) challenge iframe 变可见
                for f in d.find_elements(By.XPATH, "//iframe[contains(@title, 'challenge')]"):
                    try:
                        if f.is_displayed():
                            return True
                    except Exception:
                        pass
                # 2) 直接通过 - checkbox-checked class 出现
                try:
                    d.switch_to.frame(checkbox_frame)
                    ok = bool(d.find_elements(By.CLASS_NAME, "recaptcha-checkbox-checked"))
                    d.switch_to.default_content()
                    if ok:
                        return True
                except Exception:
                    try:
                        d.switch_to.default_content()
                    except Exception:
                        pass
                return False
            self._poll(_checkbox_response_ready, timeout=5.0)

        except Exception:
            print("[!] 未能定位/点击 reCAPTCHA 复选框")
            self.driver.switch_to.default_content()
            self._panel("未能定位/点击复选框", "error")
            self._set_attempt("失败")
            return False

        try:
            self.driver.switch_to.frame(checkbox_frame)
            is_checked = self.driver.find_elements(By.CLASS_NAME, "recaptcha-checkbox-checked")
            if is_checked and "true" in is_checked[0].get_attribute("aria-checked"):
                print("[+] 直接通过验证 (无需音频挑战)")
                self.driver.switch_to.default_content()
                self._panel("直接通过 (无需音频挑战)", "success")
                self._set_attempt("成功 ✓")
                return True
            self.driver.switch_to.default_content()
        except:
            self.driver.switch_to.default_content()

        return self._handle_audio_challenge()

    def _handle_audio_challenge(self):
        challenge_frame = None
        try:
            print("[*] 切换到音频挑战 iframe")
            self._panel("切换到音频挑战 iframe", "progress")
            challenge_frame = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//iframe[contains(@title, 'challenge')]"))
            )
            self.driver.switch_to.frame(challenge_frame)

            MAX_ATTEMPTS = 5
            # multi correct / reload 后,reCAPTCHA 会自动准备下一段 audio。
            # 此时再点 audio button 会被视为"重置当前段",反而让 audio src
            # 不再变化(实测会陷入死循环反复识别同一段)。所以下一轮要 skip。
            skip_audio_btn = False
            for attempt in range(MAX_ATTEMPTS):
                print(f"[*] 第 {attempt + 1}/{MAX_ATTEMPTS} 次音频挑战")
                self._set_attempt(f"第 {attempt + 1}/{MAX_ATTEMPTS} 次", restore_frame=challenge_frame)
                self._panel(f"第 {attempt + 1}/{MAX_ATTEMPTS} 次音频挑战", "progress", restore_frame=challenge_frame)

                # 先记录当前 audio-source.src(如果有的话),用于判断 reCAPTCHA
                # 是否加载了新一段音频。第 1 轮通常没有,prev_src=""。
                prev_src = ""
                _sources = self.driver.find_elements(By.ID, "audio-source")
                if _sources:
                    try:
                        prev_src = _sources[0].get_attribute("src") or ""
                    except Exception:
                        pass

                if not skip_audio_btn:
                    try:
                        audio_btn = WebDriverWait(self.driver, 5).until(
                            EC.element_to_be_clickable((By.ID, "recaptcha-audio-button"))
                        )
                        audio_btn.click()
                    except:
                        if len(self.driver.find_elements(By.ID, "recaptcha-reload-button")) == 0:
                            pass
                skip_audio_btn = False

                # 替代固定 sleep(2):等 audio-source.src 真正"变成新 URL"
                # (reCAPTCHA 加载完新一段音频,或第 1 轮首次出现) 或 'Try
                # again later' 出现 (被风控)。
                # !!! 不能只等 audio-source 元素出现 —— 该元素可能一直存在
                # 但 src 还是上一轮的旧 URL,会导致反复识别同一段音频。
                self._poll(
                    lambda d, ps=prev_src: (
                        any(
                            (s.get_attribute("src") or "") and (s.get_attribute("src") or "") != ps
                            for s in d.find_elements(By.ID, "audio-source")
                        ) or
                        bool(d.find_elements(By.XPATH, "//*[contains(text(), 'Try again later')]"))
                    ),
                    timeout=5.0,
                )

                if len(self.driver.find_elements(By.XPATH, "//*[contains(text(), 'Try again later')]")) > 0:
                    print("[!] Google 提示 'Try again later' —— 当前 IP 被标记为可疑,无法继续。请更换网络/降低请求频率后再试。")
                    self.driver.switch_to.default_content()
                    self._panel("Google 提示 'Try again later' —— IP 被风控", "error")
                    self._set_attempt("被风控")
                    return False

                # audio-source 通常已经在上面的 _poll 中出现,这里 0 等待拿元素;
                # 极少数情况下两个条件都没出现 (timeout),才走 except 走 break。
                try:
                    audio_source = WebDriverWait(self.driver, 2).until(
                        EC.presence_of_element_located((By.ID, "audio-source"))
                    )
                    src_url = audio_source.get_attribute("src")
                    print(f"    ├─ 已获取音频 URL: {src_url[:80]}...")
                    self._panel("已获取音频 URL", "info", restore_frame=challenge_frame)
                except:
                    print("    └─ 未能找到音频源 (audio-source),退出循环")
                    self._panel("未能找到音频源 (audio-source)", "warning")
                    break

                # 安全网:如果新拿到的 src 与上一轮完全相同,说明 reCAPTCHA
                # 已经不再切换音频 (常见于 multi correct 死循环 —— Whisper 转
                # 错被反复要求"重输",但 reCAPTCHA 不给新音频)。立即 break,
                # 避免空转 5 轮 timeout 浪费几十秒。
                if attempt > 0 and prev_src and src_url == prev_src:
                    print(f"    └─ src 与上一段相同,reCAPTCHA 不再切换音频,放弃")
                    self._panel("音频未切换,放弃", "error", restore_frame=challenge_frame)
                    break

                # 用浏览器内 fetch 下载 audio。绝不能用 Python requests:
                # Google 会通过 TLS 指纹/Cookies/Referer 判定为脚本抓取,
                # 直接把整个 reCAPTCHA session 拉黑。
                self.driver.set_script_timeout(30)
                fetch_result = self.driver.execute_async_script(_FETCH_AUDIO_JS, src_url)
                if not fetch_result or not fetch_result.get("ok"):
                    err = (fetch_result or {}).get("error", "(unknown)")
                    print(f"    └─ 浏览器内 fetch 音频失败: {err}")
                    self._panel(f"fetch 音频失败: {err}", "error", restore_frame=challenge_frame)
                    break
                audio_bytes = base64.b64decode(fetch_result["b64"])
                print(f"    ├─ 已获取 MP3 ({len(audio_bytes)} 字节)")
                self._panel(f"已获取 MP3 ({len(audio_bytes)} 字节)", "info", restore_frame=challenge_frame)

                self._panel("Whisper 识别中...", "progress", restore_frame=challenge_frame)
                self._load_model()  # 已被 _start_model_preload 后台启动,这里通常立即返回

                # 直接给 BytesIO,跳过写盘/读盘/删盘,faster-whisper 原生支持 BinaryIO
                segments, info = self.model.transcribe(io.BytesIO(audio_bytes), beam_size=5)
                text = " ".join([segment.text for segment in segments]).strip()
                print(f"    ├─ Whisper 转录结果: '{text}'")
                self._panel(f"Whisper 转录: \"{text}\"", "info", restore_frame=challenge_frame)

                try:
                    input_box = self.driver.find_element(By.ID, "audio-response")
                    input_box.clear()
                    input_box.send_keys(text.lower())
                except:
                    pass

                try:
                    verify_btn = self.driver.find_element(By.ID, "recaptcha-verify-button")
                    verify_btn.click()
                except:
                     pass

                # 替代固定 sleep(2):等结果。任一为真立即继续:
                #   a) audio error 出现且有可见文本 (转错或需多段答案)
                #   b) audio-source 元素从 DOM 消失 (验证通过,挑战即将关闭)
                # timeout 1.5s 实测足够 —— 即使超时,后面有兜底:成功路径
                # 会通过 checkbox-checked 判断;失败路径会通过 error 文本判断。
                self._poll(
                    lambda d: (
                        any(e.is_displayed() and e.text.strip()
                            for e in d.find_elements(By.CLASS_NAME, "rc-audiochallenge-error-message")) or
                        not d.find_elements(By.ID, "audio-source")
                    ),
                    timeout=1.5,
                )

                error_msgs = self.driver.find_elements(By.CLASS_NAME, "rc-audiochallenge-error-message")
                retry_needed = False
                if error_msgs and error_msgs[0].is_displayed() and error_msgs[0].text.strip():
                    err_text = error_msgs[0].text
                    if "multiple correct" in err_text.lower():
                        print(f"    └─ 提示需要多个正确答案,继续下一段音频")
                        self._panel("需要多个正确答案,继续下一段", "warning", restore_frame=challenge_frame)
                        retry_needed = True
                        # multi correct 后 reCAPTCHA 会自动加载下一段,不要再点
                        # audio_btn(否则陷入死循环)。下一轮开头 _poll 等 src 变化。
                        skip_audio_btn = True
                    else:
                        print(f"    └─ 转录被判错 ('{err_text}'),刷新音频重试")
                        self._panel(f"转录被判错 ({err_text}),刷新重试", "warning", restore_frame=challenge_frame)
                        try:
                            reload_btn = self.driver.find_element(By.ID, "recaptcha-reload-button")
                            reload_btn.click()
                            # reload 触发新音频自动加载,下一轮也跳过 audio_btn click
                            skip_audio_btn = True
                        except:
                            pass
                        continue

                if not retry_needed:
                    self.driver.switch_to.default_content()

                    try:
                        checkbox_frame = self.driver.find_element(By.XPATH, "//iframe[contains(@title, 'reCAPTCHA') and not(contains(@title, 'challenge'))]")
                        self.driver.switch_to.frame(checkbox_frame)
                        is_checked = self.driver.find_elements(By.CLASS_NAME, "recaptcha-checkbox-checked")
                        if is_checked and "true" in is_checked[0].get_attribute("aria-checked"):
                            print("[+] 验证通过 (复选框已勾选)")
                            self.driver.switch_to.default_content()
                            self._panel("验证通过 (复选框已勾选)", "success")
                            self._set_attempt("成功 ✓")
                            return True
                    except:
                        pass

                    # 兜底:这里用极短 timeout(0.5s),因为成功路径上面已经 return 了,
                    # 走到这里通常是 challenge iframe 已经消失 (验证通过) 或者
                    # checkbox 状态还没及时同步。0.5s 足够给后者反应时间。
                    try:
                        self.driver.switch_to.default_content()
                        challenge_frame = WebDriverWait(self.driver, 0.5).until(
                             EC.presence_of_element_located((By.XPATH, "//iframe[contains(@title, 'challenge')]"))
                        )
                        self.driver.switch_to.frame(challenge_frame)
                        continue
                    except:
                        print("[+] 验证通过 (挑战 iframe 已消失)")
                        self.driver.switch_to.default_content()
                        self._panel("验证通过 (挑战 iframe 已消失)", "success")
                        self._set_attempt("成功 ✓")
                        return True

            print(f"[!] 已达 {MAX_ATTEMPTS} 次尝试上限,放弃")
            self.driver.switch_to.default_content()
            self._panel(f"已达 {MAX_ATTEMPTS} 次上限,放弃", "error")
            self._set_attempt("失败")
            return False

        except Exception as e:
            err_msg = str(e).strip() or "(无具体错误信息)"
            print(f"[!] 音频挑战流程异常 [{type(e).__name__}]: {err_msg}")
            print("[i] 提示:常见原因是 reCAPTCHA 直接弹出了图像挑战(没有音频按钮)、或 iframe 加载超时、或当前 IP 被风控")
            self.driver.switch_to.default_content()
            short = err_msg.splitlines()[0][:120] if err_msg else "(无)"
            self._panel(f"流程异常 [{type(e).__name__}]: {short}", "error")
            self._set_attempt("异常")
            return False
