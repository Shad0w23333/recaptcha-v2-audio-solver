# reCAPTCHA v2 音频验证码自动求解器

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/macOS-Apple%20Silicon%20%E5%AE%9E%E6%B5%8B%E9%80%9A%E8%BF%87-brightgreen)

通过本地 AI 语音识别(`faster-whisper`)+ 浏览器自动化(Selenium / undetected-chromedriver)
自动通过 Google reCAPTCHA v2 的**音频挑战**。**不需要任何云端 API key**,完全离线推理。

> ⚠️ 仅供学习、研究、CTF 与防御性安全测试用途。请勿用于绕过任何正常的反爬保护。

---

## 目录

- [🔥 一句话总结](#-一句话总结)
- [✅ Mac 兼容性结论(本机已实测)](#-mac-兼容性结论本机已实测)
- [📦 系统要求](#-系统要求)
- [🚀 在 Mac 上的完整安装步骤](#-在-mac-上的完整安装步骤)
- [▶️ 快速开始](#️-快速开始)
- [🔧 在自己的代码里调用](#-在自己的代码里调用)
- [📁 项目结构](#-项目结构)
- [⚙️ 工作原理](#️-工作原理)
- [🐞 本次研究修复的两个真实 Bug](#-本次研究修复的两个真实-bug)
- [❓ 常见问题与故障排查](#-常见问题与故障排查)
- [📊 性能与精度建议](#-性能与精度建议)
- [📝 本机实测结果](#-本机实测结果)
- [⚖️ 法律与免责声明](#️-法律与免责声明)

---

## 🔥 一句话总结

**结论:在 macOS 26.1 / Apple Silicon (ARM64) / Python 3.10 上验证可以跑通。**
端到端首次运行(包含下载 ~89MB Whisper 模型)耗时约 **25 秒**,Whisper tiny 模型对
英文音频验证码的一次命中率在本次测试中表现良好。

---

## ✅ Mac 兼容性结论(本机已实测)

| 检查项 | 状态 | 实测值 / 说明 |
|---|---|---|
| macOS 版本 | ✅ | macOS 26.1 (Build 25B78) |
| 芯片架构 | ✅ | Apple Silicon (ARM64),M 系列 |
| Python | ✅ | 3.10.17 (Homebrew) |
| Homebrew | ✅ | 5.1.8 |
| ffmpeg | ✅ | 8.1(`faster-whisper` 的硬性依赖) |
| Google Chrome | ✅ | 147.0.7727.138 |
| 4 个核心 pip 包 | ✅ | 全部预编译 wheel 安装,**无需本地编译** |
| ChromeDriver 自动管理 | ✅ | 修复后自动对齐本机 Chrome 版本 |
| reCAPTCHA 端到端流程 | ✅ | 实测一次性 `FINAL RESULT: True` |

---

## 📦 系统要求

| 名称 | 最低版本 | 说明 |
|---|---|---|
| macOS | 11 Big Sur+ | Apple Silicon 与 Intel 均可,M 系列体验更好 |
| Python | 3.10 或更高 | 推荐通过 Homebrew 装 `python@3.10` |
| Google Chrome | 任意稳定版 | 必需,`undetected-chromedriver` 会驱动它 |
| ffmpeg | 任意现代版本 | `faster-whisper` 用它解码 MP3 |
| 磁盘空间 | ~200 MB | 含 Python 依赖和 Whisper tiny 模型(~89 MB) |
| 网络 | 可访问 Google 与 HuggingFace | 首次下载模型需要 HuggingFace |

---

## 🚀 在 Mac 上的完整安装步骤

### 1. 装 Homebrew(如果还没有)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 2. 装 Python 3.10 与 ffmpeg

```bash
brew install python@3.10 ffmpeg
```

验证:

```bash
python3.10 --version       # 应输出 Python 3.10.x
ffmpeg -version | head -1   # 应输出 ffmpeg version 7.x 或 8.x
```

### 3. 装 Google Chrome

从 [google.com/chrome](https://www.google.com/chrome/) 下载 dmg 安装即可。
本项目代码会自动检测 Chrome 主版本号并匹配下载对应的 ChromeDriver,
**不需要手动管理 chromedriver**。

### 4. 克隆/下载本项目并安装 Python 依赖

```bash
cd "/path/to/recaptcha-v2-audio-solver"
python3.10 -m pip install -r requirements.txt
```

四个核心依赖(完整列表见 [requirements.txt](requirements.txt)):

| 包名 | 作用 |
|---|---|
| `undetected-chromedriver` | 自动下载并驱动绕过反爬检测的 Chrome |
| `selenium` | 浏览器自动化框架 |
| `requests` | 下载验证码音频 MP3 |
| `faster-whisper` | OpenAI Whisper 的本地高性能推理引擎 |

> **关于虚拟环境**:本机演示直接装到系统 Python 3.10。如果你希望隔离,
> 可改用 `python3.10 -m venv .venv && source .venv/bin/activate` 后再 `pip install`。

---

## ▶️ 快速开始

直接运行自带的测试脚本:

```bash
cd "/path/to/recaptcha-v2-audio-solver"
python3.10 test_google.py
```

会发生:

1. 自动检测本机 Chrome 主版本,下载匹配的 ChromeDriver
2. 弹出一个 1920×1080 的 Chrome 窗口,导航到 reCAPTCHA 官方 demo
3. **首次运行**会从 HuggingFace 下载 Whisper tiny 模型到 [models/](models/) 目录(~89MB)
4. solver 自动点击复选框、识别音频、填写答案、点验证
5. 终端输出全程进度,最后打印 `FINAL RESULT: True/False`

**首次成功运行的真实日志(本机实测):**

```
[*] 寻找 reCAPTCHA 复选框 iframe...
[*] 已点击复选框,等待 reCAPTCHA 响应...
[*] 切换到音频挑战 iframe
[*] 第 1/5 次音频挑战
    ├─ 已获取音频 URL: https://www.google.com/recaptcha/api2/payload?p=...
    ├─ 已下载 MP3: captcha_5088.mp3 (36164 字节)
    ├─ Whisper 转录结果: 'I would recommend getting'
[+] 验证通过 (复选框已勾选)
FINAL RESULT: True
```

---

## 🔧 在自己的代码里调用

```python
from driver.chrome_driver import create_driver
from solver import AudioCaptchaSolver

driver = create_driver()
driver.get("https://www.google.com/recaptcha/api2/demo")

# 默认用 tiny 模型,放在项目目录下的 models/
solver = AudioCaptchaSolver(driver)
result = solver.solve()  # True / False

print(f"是否通过: {result}")
driver.quit()
```

可调参数:

```python
solver = AudioCaptchaSolver(
    driver,
    model_size="base",                  # tiny / base / small / medium 越大越准也越慢
    download_root="/your/cache/dir",    # 自定义模型缓存目录
)
```

---

## 📁 项目结构

```
recaptcha-v2-audio-solver/
├── README.md                  # 本文档
├── requirements.txt           # Python 依赖
├── solver.py                  # 核心求解器(含中文进度日志)
├── test_google.py             # 端到端测试脚本
├── driver/
│   └── chrome_driver.py       # Chrome 工厂(自动版本对齐 + 强制英文 locale)
└── models/                    # Whisper 模型缓存(自动创建,已加入 .gitignore)
```

| 文件 | 关键职责 |
|---|---|
| [solver.py](solver.py) | `AudioCaptchaSolver` 类,负责整个 reCAPTCHA 流程 |
| [driver/chrome_driver.py](driver/chrome_driver.py) | 创建配置好的 Chrome 实例 |
| [test_google.py](test_google.py) | 在 Google demo 页上跑一次完整流程 |
| [requirements.txt](requirements.txt) | 4 个核心依赖 |

---

## ⚙️ 工作原理

[solver.py](solver.py) 中 `solve()` 的整体流程:

1. **找到 reCAPTCHA 复选框 iframe** → 切入 → 点击 `recaptcha-anchor`
2. 等待 2 秒,**判断是否被直接通过**(没有挑战),如果是则 `return True`
3. 否则进入 `_handle_audio_challenge()`,**最多重试 5 次**:
   1. 切到挑战 iframe → 点击音频按钮(`recaptcha-audio-button`)
   2. 检测是否出现 `Try again later`(IP 被风控,直接放弃)
   3. 抓取 `<source id="audio-source">` 的 URL → `requests.get` 下载 MP3
   4. **Whisper 本地转录** → 填入 `audio-response` 输入框 → 点击验证按钮
   5. 检查错误提示:
      - `multiple correct solutions required` → 继续下一段音频
      - 其他错误 → 点击刷新按钮换一段音频重试
      - 无错误且复选框变成 `recaptcha-checkbox-checked` → `return True`

整个过程使用 `int8` 量化的 tiny 模型,**纯 CPU 推理**,无需 GPU。

---

## 🐞 本次研究修复的两个真实 Bug

在 Mac 上首次运行时遇到了两个原项目里就存在的 bug,本仓库已修复。
这些修复对所有平台都有益处。

### Bug 1:ChromeDriver 版本与本机 Chrome 不匹配

**现象**:
```
SessionNotCreatedException: This version of ChromeDriver only supports Chrome version 148
Current browser version is 147.0.7727.138
```

**原因**:`undetected-chromedriver` 默认会拉取**最新**版本的 ChromeDriver,
但用户的 Chrome 不一定是最新版,导致版本错位。

**修复**:在 [driver/chrome_driver.py](driver/chrome_driver.py) 中加入 `_detect_chrome_major_version()`
函数,自动用 `subprocess` 调用 `Google Chrome --version` 抓取主版本号,
然后传给 `uc.Chrome(version_main=N)`,让它去拉对应版本的驱动。
覆盖 macOS / Linux / Windows 的常见安装路径。

### Bug 2:中文系统下 reCAPTCHA 走中文界面,XPath 全部失效

**现象**:`TimeoutException`,`_handle_audio_challenge()` 等不到挑战 iframe。

**根因**:在中文系统上 Chrome 默认 `Accept-Language: zh-CN`,reCAPTCHA 把
挑战 iframe 的 title 翻译成 `'reCAPTCHA 验证任务将于 2 分钟后过期'`。
但 [solver.py](solver.py) 里写死的是 `XPath: //iframe[contains(@title, 'challenge')]`,
匹配不到中文标题,直接超时失败。同样的隐患还包括对 `Try again later`、
`multiple correct solutions` 这些英文文本的判断。

**修复**:在 [driver/chrome_driver.py](driver/chrome_driver.py) 中:
1. 给 Chrome 启动加 `--lang=en-US` 参数
2. 通过 CDP `Network.setExtraHTTPHeaders` 强制 `Accept-Language: en-US,en;q=0.9`

修复后 iframe 的 title 恢复为英文 `'recaptcha challenge expires in two minutes'`,
所有 XPath / 字符串判断重新生效。

> 这个 bug 在英文系统上不会出现,所以原作者大概率没有意识到。
> 任何中文 / 日文 / 韩文 / 法文等非英文系统的用户都会被它卡住。

---

## ❓ 常见问题与故障排查

### Q1:`SessionNotCreatedException: only supports Chrome version N`

✅ **本仓库已自动修复**(见上文 Bug 1)。如果仍然出现,通常是 Chrome 升级到了
更新的主版本而 `undetected-chromedriver` 缓存里还是老 driver,
删除缓存后重新跑即可:

```bash
rm -rf ~/Library/Application\ Support/undetected_chromedriver
```

### Q2:`FINAL RESULT: False` 且日志出现 `Try again later`

这是 **Google 把当前出口 IP 标记为可疑**(机房 IP / 频繁请求 / VPN 等)。
本身不是代码问题,处理方式:

- 换网络(切到家庭宽带、或换一个干净的 IP)
- 等 10–30 分钟再试(Google 通常会自动解封)
- 降低请求频率(连续多次跑 demo 容易触发)

### Q3:Whisper 模型下载失败 / 极慢

模型来自 HuggingFace。如果直连慢,可以设环境变量用国内镜像:

```bash
export HF_ENDPOINT=https://hf-mirror.com
python3.10 test_google.py
```

或者改用更小的模型(默认就是 `tiny`,这是最小的)。

### Q4:`ffmpeg: command not found`

```bash
brew install ffmpeg
```

### Q5:Apple Silicon 编译报错

本次实测 4 个核心依赖**都有 ARM64 预编译 wheel**,正常情况不会触发本地编译。
如果你遇到了编译错误,装一下 Xcode Command Line Tools:

```bash
xcode-select --install
```

### Q6:reCAPTCHA 直接弹了图像挑战(没有音频按钮)

这是 Google 主动升级难度的策略,通常和 IP 信誉相关,
和 Q2 处理方式相同——换 IP、降频。

### Q7:输出 `FINAL RESULT: True` 但浏览器里没看到绿色 ✓

solver 检查的是 reCAPTCHA 内部的 `recaptcha-checkbox-checked` class,
demo 页面有时不会同步 UI 视觉效果,**以日志为准**。

---

## 📊 性能与精度建议

`AudioCaptchaSolver(model_size=...)` 的可选模型:

| 模型 | 大小 | 速度(M 系列 CPU)| 精度 | 适用场景 |
|---|---|---|---|---|
| `tiny` (默认) | ~89 MB | < 1 秒 / 段 | 短英文清晰音频已够用 | reCAPTCHA v2 默认音频,**推荐** |
| `base` | ~290 MB | 1–2 秒 / 段 | 略好 | tiny 偶尔失败时升级 |
| `small` | ~970 MB | 3–5 秒 / 段 | 明显更好 | 噪声较大的音频 |
| `medium` | ~3 GB | 8–15 秒 / 段 | 接近最佳 | 不推荐,过度杀鸡 |

**经验建议**:reCAPTCHA 音频本身是 8–10 秒的清晰人声,`tiny` 已经足够。
模型越大下载越慢、首次推理越慢,而且会让单次重试时间变长,
反而更容易触发 Google 的"超时"判定。

代码中已经使用 `compute_type="int8"` 8-bit 量化,无需 GPU 即可获得很好的速度。

---

## 📝 本机实测结果

| 项目 | 数值 |
|---|---|
| 测试时间 | 2026-05-02 |
| 操作系统 | macOS 26.1 (Build 25B78) |
| 芯片 | Apple Silicon (ARM64) |
| Python | 3.10.17(Homebrew `python@3.10`)|
| Chrome | 147.0.7727.138 |
| ffmpeg | 8.1 |
| Whisper 模型 | `Systran/faster-whisper-tiny`,89 MB |
| 4 个核心依赖安装 | 全部预编译 wheel,**0 编译,30 秒内完成** |
| 第 1 次端到端运行 | ✅ `FINAL RESULT: True`,**1 次音频识别就通过**,总耗时 ≈ 24.6 秒(含模型下载) |
| 第 2 次端到端运行 | ⚠️ `FINAL RESULT: False`,Google 提示 `Try again later`(IP 被风控),总耗时 ≈ 18.1 秒 |
| Whisper 转录效果 | 短英文音频识别准确,例:`'I would recommend getting'` 直接通过 |

**结论**:在本机 Mac 上完全可跑通。失败用例并非代码问题,而是 Google 反作弊
机制的正常表现。换网络或等待一段时间后即可恢复。

---

## ⚖️ 法律与免责声明

- 本项目**仅供学习、研究、CTF 题目与授权范围内的安全测试**使用。
- **不要**用它去绕过任何你没有合法授权访问的网站的反爬虫保护。
- 大规模、自动化地求解第三方网站的 reCAPTCHA 可能违反 Google 服务条款,
  以及当地法律法规。
- 因使用本项目造成的任何后果,使用者自行承担。

---

> 本 README 由实际在 macOS 26.1 / M 系列芯片上从零开始安装、运行、修复 bug 的
> 完整研究过程整理而成。如果你也在其它平台/版本上跑成功或失败,欢迎补充。
