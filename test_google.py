import time
import os
import sys

# Force CWD for imports
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from driver.chrome_driver import create_driver
from solver import AudioCaptchaSolver

def test():
    driver = create_driver()

    try:
        driver.get("https://www.google.com/recaptcha/api2/demo")
        # 替代固定 sleep(3):等 reCAPTCHA 复选框 iframe 出现就立即继续。
        # 实测通常 < 1 秒,而原来固定等 3 秒。
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//iframe[contains(@title, 'reCAPTCHA')]")
                )
            )
        except Exception:
            pass

        solver = AudioCaptchaSolver(driver)
        result = solver.solve()

        print(f"FINAL RESULT: {result}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        # 留 5 秒让用户看面板的最终状态(成功/失败/被风控等)
        time.sleep(5)
        driver.quit()

if __name__ == "__main__":
    test()
