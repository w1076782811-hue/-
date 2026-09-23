#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
把「视频/音频来源」落到本地文件，打印最终本地路径到 stdout。

支持三类来源：
  1) 本地已存在路径                       -> 直接复用（不复制，省空间）
  2) 直链媒体 http(s) 且路径以媒体后缀结尾 -> urllib 直接下载
  3) 平台页面链接（B站/抖音/小红书/微博/YouTube/快手…）-> 调 yt-dlp 下载

通用版：所有路径从 <skill>/config.json 读取（可用环境变量覆盖），不含任何个人信息。

用法:
  fetch_media.py <来源> <输出目录> [--audio-only] [--max-height 1080]
                 [--cookies <cookies.txt>] [--cookies-from-browser chrome]
                 [--proxy http://127.0.0.1:7897]

  · --audio-only      只下载音频（转写用，省流量省空间）；缺省下载完整视频
  · --max-height      完整视频时的最高画质，默认 1080（--audio-only 时忽略）
  · 境外站点（YouTube 等）会自动走代理：优先 --proxy，其次 config.json 的 proxy，再其次环境变量 VTT_PROXY
  · <输出目录> 只是落地目录，文件名由 yt-dlp 按视频标题生成；stdout 打印真实落地路径
"""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent


def load_config():
    cfg = {}
    p = SKILL_DIR / "config.json"
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    return cfg


CFG = load_config()
PYTHON = os.environ.get("VTT_PYTHON", CFG.get("python", "python"))
BIN_DIR = os.environ.get("VTT_BIN_DIR", CFG.get("bin_dir", ""))
PROXY = os.environ.get("VTT_PROXY", CFG.get("proxy", ""))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

MEDIA_EXTS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".flv", ".avi", ".ts", ".wmv", ".mpg", ".mpeg",
    ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus", ".wma", ".amr",
}

# 需要走代理的域名（大陆直连不通或极慢）
PROXY_HOSTS = ("youtube.com", "youtu.be", "googlevideo.com", "ytimg.com",
               "ggpht.com", "googleusercontent.com")


class _NeedsYtdlp(Exception):
    """直链下载拿到的是网页而不是媒体文件，需要交给 yt-dlp 解析。"""


def normalize_url(url: str) -> str:
    """把各种「分享页/精选页」链接归一化成提取器认得的规范形式。

    抖音尤其重要：yt-dlp 的 DouyinIE 只认 /video/<数字ID>，
    而 App/网页分享出来的常是 /jingxuan?modal_id=<ID>、/?modal_id=<ID>
    或 v.douyin.com 短链，直接喂会报 Unsupported URL。
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return url
    host = (parts.netloc or "").lower()
    if "douyin.com" not in host:
        return url

    m = re.search(r"/video/(\d+)", parts.path)
    if m:
        return "https://www.douyin.com/video/" + m.group(1)

    q = urllib.parse.parse_qs(parts.query)
    for key in ("modal_id", "aweme_id", "vid"):
        vals = q.get(key) or []
        if vals and str(vals[0]).isdigit():
            return "https://www.douyin.com/video/" + str(vals[0])

    tail = parts.path.rstrip("/").rsplit("/", 1)[-1]
    if tail.isdigit() and len(tail) >= 15:
        return "https://www.douyin.com/video/" + tail

    return url


def is_url(s: str) -> bool:
    return s.lower().startswith("http://") or s.lower().startswith("https://")


def is_direct_media_url(url: str) -> bool:
    path = urllib.parse.urlsplit(url).path.lower()
    return os.path.splitext(path)[1] in MEDIA_EXTS


def host_needs_proxy(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).netloc or "").lower()
    return any(h in host for h in PROXY_HOSTS)


def looks_like_token(s: str) -> bool:
    # 不存在的本地文件，且不含路径分隔符 -> 视为飞书 file_token
    if os.path.exists(s):
        return False
    if os.sep in s or (os.altsep and os.altsep in s):
        return False
    return True


# ---------------------------------------------------------------- 直链下载

def download_url(url: str, out_path: str):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "html" in ctype or ctype.startswith("text/"):
            raise _NeedsYtdlp("直链返回的是网页(Content-Type: %s)，改由 yt-dlp 解析" % (ctype or "unknown"))
        with open(out_path, "wb") as f:
            shutil.copyfileobj(resp, f)
    print("已下载: %s -> %s" % (url, out_path), file=sys.stderr)


# ---------------------------------------------------------------- yt-dlp 下载

def download_with_ytdlp(url: str, out_dir: str, audio_only: bool = False,
                        max_height: str = "1080",
                        cookies_from_browser: str = None, cookies_file: str = None,
                        proxy: str = None) -> str:
    """用 yt-dlp 下载平台页面上的视频，返回落地文件路径。"""
    py = PYTHON
    if os.sep in py:
        if not os.path.exists(py):
            raise RuntimeError(
                "找不到 yt-dlp 运行环境（config.json 里的 python=%s）。\n"
                "请先运行 scripts/setup.py 完成自举。" % py)
    elif not shutil.which(py):
        raise RuntimeError("找不到 python，请先运行 scripts/setup.py 完成自举。")

    os.makedirs(out_dir, exist_ok=True)
    url = normalize_url(url)

    # 境外站点自动带代理（B站/抖音直连更快，不加）
    if not proxy and host_needs_proxy(url):
        proxy = PROXY
        if proxy:
            print("检测到境外站点，自动使用代理: %s" % proxy, file=sys.stderr)

    cmd = [py, "-m", "yt_dlp",
           "--no-warnings", "--no-playlist",
           "--no-simulate", "--print", "after_move:filepath"]

    if audio_only:
        cmd += ["-f", "bestaudio/best"]
    else:
        h = str(max_height or "1080")
        cmd += ["-f", "bv*[height<=%s]+ba/b[height<=%s]/b" % (h, h),
                "--merge-output-format", "mp4"]

    cmd += ["-o", os.path.join(out_dir, "%(title).80s.%(ext)s")]
    if os.name == "nt":
        cmd += ["--windows-filenames"]
    if BIN_DIR and os.path.isdir(BIN_DIR):
        cmd += ["--ffmpeg-location", BIN_DIR]
    if proxy:
        cmd += ["--proxy", proxy]
    if cookies_file and os.path.exists(cookies_file):
        cmd += ["--cookies", cookies_file]
    elif cookies_file:
        print("提示: cookie 文件不存在(%s)，按无 cookie 继续" % cookies_file, file=sys.stderr)
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    cmd.append(url)

    mode = "音频" if audio_only else "完整视频"
    print("调用 yt-dlp 下载 %s…" % mode, file=sys.stderr)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)

    if r.returncode != 0:
        tail = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()[-2000:]
        raise RuntimeError("yt-dlp 下载失败（返回码 %s）:\n%s" % (r.returncode, tail))

    lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    path = lines[-1] if lines else ""

    if not path or not os.path.exists(path):
        cands = []
        for name in os.listdir(out_dir):
            p = os.path.join(out_dir, name)
            if os.path.splitext(name)[1].lower() in MEDIA_EXTS and os.path.isfile(p):
                cands.append(p)
        if cands:
            path = max(cands, key=os.path.getmtime)

    if not path or not os.path.exists(path):
        raise RuntimeError("yt-dlp 执行完成但未找到下载文件。目录: %s" % out_dir)

    print("yt-dlp 已下载: %s" % path, file=sys.stderr)
    return path


# ---------------------------------------------------------------- 入口

def main():
    args = sys.argv[1:]
    opts, positional = {}, []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--max-height", "--cookies", "--cookies-from-browser", "--proxy") and i + 1 < len(args):
            opts[a.lstrip("-").replace("-", "_")] = args[i + 1]
            i += 2
            continue
        if a == "--audio-only":
            opts["audio_only"] = True
            i += 1
            continue
        positional.append(a)
        i += 1

    if len(positional) < 2:
        print("用法: fetch_media.py <来源> <输出目录> [--audio-only] [--max-height 1080] "
              "[--cookies <cookies.txt>] [--cookies-from-browser chrome] "
              "[--proxy http://127.0.0.1:7897]", file=sys.stderr)
        sys.exit(1)

    source, out_dir = positional[0], positional[1]

    if os.path.exists(source):
        print(source)  # 本地文件直接复用
        return

    if is_url(source):
        if is_direct_media_url(source):
            out_path = os.path.join(out_dir, os.path.basename(urllib.parse.urlsplit(source).path))
            try:
                download_url(source, out_path)
                print(out_path)
                return
            except _NeedsYtdlp as e:
                print(str(e), file=sys.stderr)

        url = normalize_url(source)
        if url != source:
            print("链接已归一化: %s" % url, file=sys.stderr)

        path = download_with_ytdlp(
            source, out_dir,
            audio_only=bool(opts.get("audio_only")),
            max_height=opts.get("max_height", "1080"),
            cookies_from_browser=opts.get("cookies_from_browser"),
            cookies_file=opts.get("cookies"),
            proxy=opts.get("proxy"),
        )
        print(path)
        return

    if looks_like_token(source):
        print("检测到疑似飞书 file_token。本脚本不直接处理飞书附件，"
              "请参考 SKILL.md 用 lark-cli 的专用命令下载。", file=sys.stderr)
        sys.exit(2)

    print("无法识别的来源: %s" % source, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
