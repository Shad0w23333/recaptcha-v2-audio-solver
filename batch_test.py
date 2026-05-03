"""连续运行 N 次 test_google 流程并汇总结果。

用法:
    python3.10 batch_test.py            # 默认 10 次,每次间隔 8 秒
    python3.10 batch_test.py 5 15       # 跑 5 次,每次间隔 15 秒

每次运行均启动一个全新的 Chrome 进程,模拟真实用户场景。
注意:连续运行同一 IP 上的 reCAPTCHA demo 极易触发 Google 风控
('Try again later'),这是反作弊机制的正常表现,不是代码问题。
"""

import os
import sys
import time

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from driver.chrome_driver import create_driver
from solver import AudioCaptchaSolver


def run_once(idx, total):
    print(f"\n{'=' * 50}")
    print(f"  第 {idx}/{total} 次运行")
    print(f"{'=' * 50}")

    driver = None
    t0 = time.time()
    record = {"idx": idx, "result": None, "elapsed": 0.0, "reason": ""}
    try:
        driver = create_driver()
        driver.get("https://www.google.com/recaptcha/api2/demo")
        time.sleep(3)

        solver = AudioCaptchaSolver(driver)
        result = solver.solve()
        record["result"] = result
        record["reason"] = "PASS" if result else "FAIL (见日志)"
    except Exception as e:
        record["result"] = None
        record["reason"] = f"EXCEPTION [{type(e).__name__}]: {e}"
        print(f"[!] 顶层异常: {e}")
    finally:
        record["elapsed"] = time.time() - t0
        if driver:
            try:
                # 留 2 秒便于人眼看到面板最终状态
                time.sleep(2)
                driver.quit()
            except Exception:
                pass

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
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 8

    print(f"[i] 计划运行 {n_runs} 次,每次间隔 {interval} 秒")
    print(f"[i] 估计总耗时 ≈ {n_runs * 30 + (n_runs - 1) * interval} 秒")

    records = []
    t_start = time.time()
    for i in range(1, n_runs + 1):
        records.append(run_once(i, n_runs))
        if i < n_runs:
            print(f"\n[i] 等待 {interval} 秒后继续...")
            time.sleep(interval)

    summarize(records, time.time() - t_start)


if __name__ == "__main__":
    main()
