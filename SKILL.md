---
name: 视频转文字
description: 将视频或音频（本地文件 / 直链 / B站·抖音·小红书·微博·YouTube等平台页面链接）里的说话内容识别成中文或英文文字稿，可带时间戳分段。当用户说"识别这个附件/把这段视频转成文字/转写录屏/这个链接的视频转文字/视频转文字稿/extract audio text/transcribe/audio or video to text"时使用。这是一个通用、可发布的技能：不含任何发布者个人信息，首次导入会自动完成环境自举（建隔离环境、装依赖、下载模型并自测），下载视频与转写稿的存放路径在首次使用时询问用户。
agent_created: true
version: 1.0.0
---

# 视频转文字（通用版，可发布、可自举）

把任意视频/音频里的说话内容，转写成中文或英文文字稿。引擎用 FunASR(SenseVoice)，纯 CPU 运行，能自动识别中英文，无需 GPU。

本技能**不包含任何发布者个人信息**：所有路径都在首次使用时动态询问用户或写入 `config.json`，脚本里没有任何写死的用户名/盘符。

## 首次使用（必须先做，一次性）

首次被调用时，本技能目录下还没有 `config.json`（或 `config.json` 里的 `python` 为空）。此时按下面顺序执行：

### 1) 先问用户两件事
用提问工具（AskUserQuestion）一次性问清楚：

1. **输出目录**：下载的视频、转写出来的文字稿放在哪个根目录？（给几个默认候选，如「我的文档/视频转文字输出」「桌面/视频转文字输出」，同时允许用户自己填一个绝对路径）
2. **是否同意自动下载依赖与模型**：首次自举要下载约 1~2 GB（torch + funasr + SenseVoice 模型等），询问用户是否同意、以及是否愿意装到某个盘（磁盘紧张时可指定）。

### 2) 写入 config.json
把用户选的输出目录写进 `config.json` 的 `media_dir` 字段（保持 `ensure_ascii=False`，UTF-8）。其余字段由 `setup.py` 自动生成。

### 3) 跑自举 + 首次自测
在「任意一个 python3（>=3.9）」下运行（先用 `python --version` 或 `python3 --version` 探测哪个可用）：
```bash
python scripts/setup.py
```
脚本会自动：建隔离 venv → 装依赖（torch CPU / funasr / modelscope / imageio-ffmpeg / yt-dlp）→ 下载模型（SenseVoiceSmall + fsmn-vad）→ 准备 ffmpeg → 冒烟自测（加载模型转写一段静音）。全部通过后写入 `config.json`，并打印「自举完成」。

> 自举耗时取决于网速，一般几分钟到十几分钟。**务必等它跑完**（用后台运行 `run_in_background`，完成后读输出确认出现「冒烟自测通过」）。
> 自举是幂等的，中断后重跑会自动跳过已完成的步骤。

### 4) 自测通过后
向用户报告「环境已就绪」，并说明之后只需「贴链接」即可转写。

## 标准转写流程（每次）

### 第 0 步：确认环境
读 `config.json`，确认 `python` / `models_cache_dir` 字段已存在。若为空，回到「首次使用」。

### 第 1 步：用户给出链接后，先问「下视频还是只下音频」
用户贴来平台链接（B站/抖音/YouTube…）时，用提问工具问用户：
- **下载完整视频**（保留视频文件，适合还要看画面/剪素材）
- **只下载音频**（转写用，更省流量、更省空间、更快）

> 本地文件、直链、飞书附件等来源不需要问，直接转写。

### 第 2 步：落地本地文件
用 `fetch_media.py`（通用 python 即可，脚本内部会调用 config.json 里那个 venv python 跑 yt-dlp）：
```bash
python scripts/fetch_media.py "<来源>" "<输出目录>" [--audio-only] [--max-height 1080] [--proxy http://127.0.0.1:7897]
```
- `<输出目录>` 用 `config.json` 里的 `media_dir`。
- 贴的是平台链接、且用户选了「只下音频」→ 加 `--audio-only`。
- **stdout 会打印真实落地路径**，把那一行喂给第 3 步。
- 支持的来源：本地路径 / 直链 / 平台页面链接（B站·抖音·小红书·微博·YouTube·快手等）。
- **YouTube 等境外站点必须走代理**：`fetch_media.py` 会自动用 `config.json` 的 `proxy`（或 `--proxy` 指定、或环境变量 `VTT_PROXY`）。若用户没有代理，如实告知 YouTube 下不了，国内站点（B站/抖音）不受影响。

### 第 3 步：转写
```bash
python scripts/transcribe.py "<落地路径>" [--timestamps]
```
- stdout 就是纯文字稿（日志在 stderr）。**务必把 stdout 重定向到文件再读**，长稿会被命令回显截断：
  ```bash
  python scripts/transcribe.py "<路径>" --timestamps > "<media_dir>/转写稿.txt" 2>"<media_dir>/转写.log"
  ```
- 加 `--timestamps` 得到 `[mm:ss.xx - mm:ss.xx] 文本` 的分段稿；不加则是一整段纯文本。
- 长视频（>5 分钟）用后台运行，完成后读文件。

### 第 4 步：清理临时文件
转写完成、拿到文字稿后，**删除本次下载的临时媒体**（尤其是「只下音频」场景的音频文件、以及转写过程产生的临时 log）。若用户选了「下载完整视频」并想保留视频，则保留视频文件、只删临时 log。**不要删用户自己原本给过来的本地源文件。**

### 第 5 步：交付
- 默认在聊天窗口直接呈现文字稿正文；长稿附一句说明「来源 / 时长 / 是否含时间戳 / 共多少字」。
- 用户要 Word 时，把转写稿原样（**不要改写**）排版成 .docx 交付。

## 输出目录结构
`config.json` 的 `media_dir` 下会累积：
- 下载的视频 / 音频文件（文件名 = 视频标题）
- `*-转写稿.txt`（带时间戳 / 纯文本）

## 环境变量（覆盖 config.json，换机/换盘时用）
- `VTT_RUNTIME_DIR`：隔离环境根目录（venv + models + bin），setup.py 读取
- `VTT_PYTHON`：venv 内 python 路径，fetch_media.py 读取
- `VTT_MODELS_CACHE`：模型缓存根目录，transcribe.py 读取
- `VTT_BIN_DIR`：ffmpeg 目录，fetch_media.py 读取
- `VTT_PROXY`：境外站点代理，fetch_media.py 读取

## 排错
- **`找不到 python` / `找不到 yt-dlp 运行环境`** → 还没自举，先跑 `python scripts/setup.py`。
- **转写中文但期望英文**：SenseVoice 会自动判断语言，无需配置；中英混读也能应付。
- **内存不足（段错误 / not enough memory）**：SenseVoice 模型约 900MB，加载+推理峰值约需 2.3GB 连续空闲内存。关掉无关程序再试；或换更小的 paraformer 模型（会丢数字规整）。
- **平台链接 `Unsupported URL`**：抖音分享链接形态多变，`fetch_media.py` 已内置归一化；若又冒出新形态，扩展 `normalize_url()`。
- **抖音报 `Fresh cookies needed`**：抖音下载需要新鲜 cookie。用 `--cookies-from-browser chrome`（若浏览器 cookie 未加密）或让用户导出 cookie 文件后用 `--cookies <file>` 传入。
- **YouTube 报 `Unable to connect to proxy` / 超时**：没配代理。让用户提供一个可用代理端口，写进 `config.json` 的 `proxy`（或 `--proxy` 传入）。
- **`ffmpeg not found` / 合并失败**：setup.py 会自动准备 ffmpeg 到 `runtime/bin`；丢了就重跑 `setup.py`。
- **长稿被截断**：不要把转写 stdout 直接丢给工具回显，`> 文件` 落盘后读文件。
- **自举下载模型失败**：模型来自 ModelScope，需网络可达 modelscope.cn；不通则重试或换镜像。
- **Windows 下脚本里写死盘符路径**：本技能所有路径都动态获取，请勿在脚本里硬编码任何 `C:\Users\...` 之类路径（会破坏「可发布、无个人信息」这一前提）。

## 发布 / 移植说明
- 整个 `video-to-text` 文件夹就是完整技能包，可直接打包成 zip 发给任何人，或放到 GitHub 仓库。
- 接收方的 Agent 拿到后，按「首次使用」章节自举即可，**无需手动安装任何东西**。
- 唯一前置条件是接收方机器上要有任意一个 Python 3.9+。
