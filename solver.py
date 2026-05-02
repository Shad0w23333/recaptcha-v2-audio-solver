import os
import time
import random
import requests
import traceback
from faster_whisper import WhisperModel
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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

    def solve(self):
        try:
            print("[*] 寻找 reCAPTCHA 复选框 iframe...")
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

            time.sleep(2)

        except Exception:
            print("[!] 未能定位/点击 reCAPTCHA 复选框")
            self.driver.switch_to.default_content()
            return False

        try:
            self.driver.switch_to.frame(checkbox_frame)
            is_checked = self.driver.find_elements(By.CLASS_NAME, "recaptcha-checkbox-checked")
            if is_checked and "true" in is_checked[0].get_attribute("aria-checked"):
                print("[+] 直接通过验证 (无需音频挑战)")
                self.driver.switch_to.default_content()
                return True
            self.driver.switch_to.default_content()
        except:
            self.driver.switch_to.default_content()

        return self._handle_audio_challenge()

    def _handle_audio_challenge(self):
        try:
            print("[*] 切换到音频挑战 iframe")
            challenge_frame = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//iframe[contains(@title, 'challenge')]"))
            )
            self.driver.switch_to.frame(challenge_frame)

            MAX_ATTEMPTS = 5
            for attempt in range(MAX_ATTEMPTS):
                print(f"[*] 第 {attempt + 1}/{MAX_ATTEMPTS} 次音频挑战")
                try:
                    audio_btn = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.ID, "recaptcha-audio-button"))
                    )
                    audio_btn.click()
                except:
                    if len(self.driver.find_elements(By.ID, "recaptcha-reload-button")) == 0:
                        pass

                time.sleep(2)

                if len(self.driver.find_elements(By.XPATH, "//*[contains(text(), 'Try again later')]")) > 0:
                    print("[!] Google 提示 'Try again later' —— 当前 IP 被标记为可疑,无法继续。请更换网络/降低请求频率后再试。")
                    self.driver.switch_to.default_content()
                    return False

                try:
                    audio_source = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((By.ID, "audio-source"))
                    )
                    src_url = audio_source.get_attribute("src")
                    print(f"    ├─ 已获取音频 URL: {src_url[:80]}...")
                except:
                    print("    └─ 未能找到音频源 (audio-source),退出循环")
                    break

                mp3_path = f"captcha_{random.randint(1000,9999)}.mp3"
                try:
                    r = requests.get(src_url)
                    with open(mp3_path, 'wb') as f:
                        f.write(r.content)
                    print(f"    ├─ 已下载 MP3: {mp3_path} ({len(r.content)} 字节)")

                    self._load_model()

                    segments, info = self.model.transcribe(mp3_path, beam_size=5)
                    text = " ".join([segment.text for segment in segments]).strip()
                    print(f"    ├─ Whisper 转录结果: '{text}'")
                finally:
                    if os.path.exists(mp3_path):
                        os.remove(mp3_path)
                
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
                
                time.sleep(2)
                
                error_msgs = self.driver.find_elements(By.CLASS_NAME, "rc-audiochallenge-error-message")
                retry_needed = False
                if error_msgs and error_msgs[0].is_displayed() and error_msgs[0].text.strip():
                    err_text = error_msgs[0].text
                    if "multiple correct" in err_text.lower():
                        print(f"    └─ 提示需要多个正确答案,继续下一段音频")
                        retry_needed = True
                        time.sleep(2)
                    else:
                        print(f"    └─ 转录被判错 ('{err_text}'),刷新音频重试")
                        try:
                            reload_btn = self.driver.find_element(By.ID, "recaptcha-reload-button")
                            reload_btn.click()
                            time.sleep(2)
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
                            return True
                    except:
                        pass

                    try:
                        self.driver.switch_to.default_content()
                        challenge_frame = WebDriverWait(self.driver, 2).until(
                             EC.presence_of_element_located((By.XPATH, "//iframe[contains(@title, 'challenge')]"))
                        )
                        self.driver.switch_to.frame(challenge_frame)
                        continue
                    except:
                        print("[+] 验证通过 (挑战 iframe 已消失)")
                        return True

            print(f"[!] 已达 {MAX_ATTEMPTS} 次尝试上限,放弃")
            self.driver.switch_to.default_content()
            return False

        except Exception as e:
            err_msg = str(e).strip() or "(无具体错误信息)"
            print(f"[!] 音频挑战流程异常 [{type(e).__name__}]: {err_msg}")
            print("[i] 提示:常见原因是 reCAPTCHA 直接弹出了图像挑战(没有音频按钮)、或 iframe 加载超时、或当前 IP 被风控")
            self.driver.switch_to.default_content()
            return False
