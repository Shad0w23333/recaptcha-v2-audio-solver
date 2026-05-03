"""连续运行 N 次 reCAPTCHA 求解流程并汇总结果。

用法:
    python3.10 batch_test.py            # 默认 10 次,每次间隔 5 秒
    python3.10 batch_test.py 5 10       # 跑 5 次,每次间隔 10 秒

**整个 batch 全程复用同一个 Chrome 进程和 solver 实例**:
- Chrome 只启动一次 (~5s),后续每次只 driver.get(demo_url) 重置页面 (~1s)
- Whisper 模型只加载一次 (异步预加载),后续 transcribe 直接用
- 复用 user-data-dir 累积 Google 信任度

相比每次 fresh driver,单次耗时从 ~14s 降到 ~6-7s。
"""

import os
import sys
import time

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from driver.chrome_driver import create_driver
from solver import AudioCaptchaSolver


DEMO_URL = "https://www.google.com/recaptcha/api2/demo"


def run_once(driver, solver, idx, total):
    print(f"\n{'=' * 50}")
    print(f"  第 {idx}/{total} 次运行")
    print(f"{'=' * 50}")

    t0 = time.time()
    record = {"idx": idx, "result": None, "elapsed": 0.0, "reason": ""}
    try:
        driver.get(DEMO_URL)
        # 等 reCAPTCHA iframe 出现 (替代固定 sleep,通常 < 1 秒)
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//iframe[contains(@title, 'reCAPTCHA')]")
                )
            )
        except Exception:
            pass

        result = solver.solve()
        record["result"] = result
        record["reason"] = "PASS" if result else "FAIL (见日志)"
    except Exception as e:
        record["result"] = None
        record["reason"] = f"EXCEPTION [{type(e).__name__}]: {e}"
        print(f"[!] 顶层异常: {e}")
    finally:
        record["elapsed"] = time.time() - t0

    flag = {True: "✓ PASS", False: "✗ FAIL", None: "! ERROR"}[record["result"]]
    print(f"\n[run {idx}] {flag}  耗时 {record['elapsed']:.1f}s  ({record['reason']})")
    return record


def summarize(records, total_wall_time):
    print(f"\n{'=' * 50}")
    print(f"  汇总 ({len(records)} 次运行)")
    print(f"{'=' * 50}")

    n_pass = sum(1 for r in records if r["result"] is True)
    n_fail = sum(1 for r in records if r["result"] is False)
    n_err  = sum(1 for r in records if r["result"] is None)
    times  = [r["elapsed"] for r in records]
    avg    = sum(times) / len(times) if times else 0.0

    print(f"  成功:  {n_pass}/{len(records)}  ({100 * n_pass / len(records):.0f}%)")
    print(f"  失败:  {n_fail}/{len(records)}")
    print(f"  异常:  {n_err}/{len(records)}")
    print(f"  单次平均耗时: {avg:.1f}s   最短 {min(times):.1f}s   最长 {max(times):.1f}s")
    print(f"  总挂钟耗时: {total_wall_time:.1f}s ({total_wall_time/60:.1f} min)")

    print(f"\n  逐次明细:")
    for r in records:
        flag = {True: "PASS  ", False: "FAIL  ", None: "ERROR "}[r["result"]]
        print(f"    #{r['idx']:2d}  {flag}  {r['elapsed']:5.1f}s   {r['reason']}")


def main():
    n_runs   = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    print(f"[i] 计划运行 {n_runs} 次,每次间隔 {interval} 秒")
    print(f"[i] 全程复用同一个 Chrome 进程,只启动 / 关闭一次")

    driver = None
    records = []
    t_start = time.time()
    try:
        # Chrome 启动一次,后续每次 run 共用
        print(f"\n[i] 启动 Chrome ...")
        driver = create_driver()

        # solver 也只创建一次,Whisper 模型加载后被所有 run 复用
        solver = AudioCaptchaSolver(driver)

        for i in range(1, n_runs + 1):
            records.append(run_once(driver, solver, i, n_runs))
            if i < n_runs:
                print(f"\n[i] 等待 {interval} 秒后继续...")
                time.sleep(interval)
    finally:
        if driver:
            try:
                # 留 5 秒看面板最终状态
                time.sleep(5)
                driver.quit()
            except Exception:
                pass

    summarize(records, time.time() - t_start)


if __name__ == "__main__":
    main()
