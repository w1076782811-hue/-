#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
视频转文字 Skill —— 一键自举脚本（bootstrap）

用「任意一个 python3（>=3.9）」运行本脚本，它会自动完成环境准备：
  1. 建一个隔离的 venv（默认 <skill>/runtime/venv，可用环境变量 VTT_RUNTIME_DIR 覆盖）
  2. 在 venv 里装齐依赖：torch(CPU) + funasr + modelscope + imageio-ffmpeg + yt-dlp
  3. 下载 ASR 模型：SenseVoiceSmall（约 900MB）+ fsmn-vad（到 <skill>/runtime/models）
  4. 准备好 yt-dlp 合并音视频轨需要的 ffmpeg（到 <skill>/runtime/bin）
  5. 跑一次冒烟自测，确认「模型可加载、推理可运行」

幂等：已装好的步骤会自动跳过，重复运行不会重复下载。

用法：
  python scripts/setup.py          # 常规自举 + 自测
  python scripts/setup.py --check  # 只检查当前状态，不安装

运行完成后，把环境路径写进 <skill>/config.json，供 fetch_media.py / transcribe.py 读取。
本脚本只依赖 Python 标准库，不需要任何第三方包。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = Path(os.environ.get("VTT_RUNTIME_DIR", SKILL_DIR / "runtime"))
VENV_DIR = RUNTIME_DIR / "venv"
BIN_DIR = RUNTIME_DIR / "bin"

# modelscope 会把模型下载到 <cache_dir>/models/<model_id 的 / 换成 -->
MODEL_LIST = [
    "iic/SenseVoiceSmall",
    "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
]

PACKAGES = ["torch", "funasr", "modelscope", "imageio-ffmpeg", "yt-dlp"]


def venv_python() -> Path:
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def py_lit(s):
    """把任意对象转成合法的 Python 字符串/列表字面量，避免引号、反斜杠转义问题。"""
    return json.dumps(s)


def run(cmd):
    print("  $ " + " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True)


def step(msg):
    print("\n=== %s ===" % msg, flush=True)


def models_ready():
    """判断两个模型是否已下载到缓存根下（modelscope 的 iic-- 命名）。"""
    sense = RUNTIME_DIR / "models" / "iic--SenseVoiceSmall"
    vad = RUNTIME_DIR / "models" / "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch"
    return sense.exists() and vad.exists()


def ensure_venv():
    if venv_python().exists():
        print("  venv 已存在，跳过创建")
        return
    step("创建隔离 venv")
    run([sys.executable, "-m", "venv", str(VENV_DIR)])


def ensure_packages():
    step("安装依赖（torch CPU + funasr + modelscope + imageio-ffmpeg + yt-dlp）")
    py = str(venv_python())
    run([py, "-m", "pip", "install", "--upgrade", "pip"])
    # torch 走 pypi 默认即 CPU 版（2.x 起不含 CUDA），无需额外 index；如需 GPU 请自行调整。
    run([py, "-m", "pip", "install", "--no-warn-script-location", *PACKAGES])


def ensure_models():
    if models_ready():
        print("  模型已存在，跳过下载")
        return
    step("下载 ASR 模型（SenseVoiceSmall 约 900MB + fsmn-vad）")
    py = str(venv_python())
    code = (
        "from modelscope import snapshot_download\n"
        "for m in %s:\n"
        "    print('downloading', m, flush=True)\n"
        "    snapshot_download(m, cache_dir=%s)\n"
        "print('MODELS_DONE', flush=True)\n"
    ) % (py_lit(MODEL_LIST), py_lit(str(RUNTIME_DIR)))
    run([py, "-c", code])
    if not models_ready():
        raise RuntimeError("模型下载后未在预期位置找到，请检查 %s" % (RUNTIME_DIR / "models"))


def ensure_ffmpeg():
    ffmpeg_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    target = BIN_DIR / ffmpeg_name
    if target.exists():
        print("  ffmpeg 已就绪，跳过")
        return
    step("准备 yt-dlp 需要的 ffmpeg（硬链接自 imageio-ffmpeg）")
    py = str(venv_python())
    code = "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
    src = subprocess.check_output([py, "-c", code], text=True).strip()
    if not src:
        raise RuntimeError("未找到 imageio-ffmpeg 的 ffmpeg 二进制")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, str(target))
    except OSError:
        shutil.copy2(src, str(target))
    print("  ffmpeg -> %s" % target)


def smoke_test():
    step("冒烟自测：加载模型 + 转写 0.5 秒静音")
    py = str(venv_python())
    wav = RUNTIME_DIR / "_smoke.wav"
    # 用标准库 wave 生成 0.5 秒 16k 单声道静音 wav（无需 numpy）
    import wave
    with wave.open(str(wav), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00\x00" * 8000)

    # 与 transcribe.py 相同的加载方式：模型名 + hub="ms" + MODELSCOPE_CACHE 指到缓存根
    code = (
        "import os\n"
        "os.environ['MODELSCOPE_CACHE'] = %s\n"
        "os.environ['MODELSCOPE_LOCAL_CACHE'] = %s\n"
        "from funasr import AutoModel\n"
        "m = AutoModel(model='iic/SenseVoiceSmall', vad_model='fsmn-vad', "
        "vad_kwargs={'max_single_segment_time': 30000}, hub='ms', "
        "disable_update=True, device='cpu')\n"
        "res = m.generate(input=%s, language='auto', use_itn=True)\n"
        "print('SMOKE_OK', type(res).__name__, len(res), flush=True)\n"
    ) % (py_lit(str(RUNTIME_DIR)), py_lit(str(RUNTIME_DIR)), py_lit(str(wav)))
    out = subprocess.check_output([py, "-c", code], text=True)
    print(out.strip())
    if "SMOKE_OK" not in out:
        raise RuntimeError("冒烟自测未通过，请查看上方输出")
    if wav.exists():
        wav.unlink()
    print("  冒烟自测通过：模型可加载、推理可运行")


def write_config():
    step("写入 config.json")
    cfg_path = SKILL_DIR / "config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    cfg["runtime_dir"] = str(RUNTIME_DIR)
    cfg["python"] = str(venv_python())
    cfg["models_cache_dir"] = str(RUNTIME_DIR)
    cfg["bin_dir"] = str(BIN_DIR)
    # media_dir 是用户偏好（由 Agent 首次引导时询问后写入），这里只补默认，不覆盖已有值
    cfg.setdefault("media_dir", str(Path.home() / "视频转文字输出"))
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  已写入 %s" % cfg_path)


def check():
    py_ok = venv_python().exists()
    m_ok = models_ready()
    print("venv:             %s" % ("OK" if py_ok else "缺失"))
    print("SenseVoiceSmall:  %s" % ("OK" if (RUNTIME_DIR / "models" / "iic--SenseVoiceSmall").exists() else "缺失"))
    print("fsmn-vad:         %s" % ("OK" if (RUNTIME_DIR / "models" / "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch").exists() else "缺失"))
    return py_ok and m_ok


def main():
    if sys.version_info < (3, 9):
        print("需要 Python >= 3.9，当前 %s" % sys.version.split()[0], file=sys.stderr)
        sys.exit(1)
    if "--check" in sys.argv:
        ok = check()
        sys.exit(0 if ok else 1)
    ensure_venv()
    ensure_packages()
    ensure_models()
    ensure_ffmpeg()
    smoke_test()
    write_config()
    print("\n自举完成：现在可以用 fetch_media.py 下载、transcribe.py 转写了。")


if __name__ == "__main__":
    main()
