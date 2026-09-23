#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
用 FunASR(SenseVoice) 把音频/视频转写成文字稿，stdout 输出纯文字。

通用版：模型缓存目录从 <skill>/config.json 读取（可用环境变量覆盖），不含个人信息。

用法:
  transcribe.py <媒体路径> [--timestamps]

  · 不加 --timestamps：输出整段纯文本（一句话连成一段）
  · 加 --timestamps：每行 [mm:ss.xx - mm:ss.xx] 文本（按 VAD 自然切块）
  · stdout 是纯稿件；进度/日志走 stderr，下游不用手写过滤
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# 修复 torch(MKL) 与 OpenMP 重复库冲突导致的段错误
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"

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
MODEL_CACHE = os.environ.get("VTT_MODELS_CACHE", CFG.get("models_cache_dir", ""))

if MODEL_CACHE:
    # 关键：funasr 用模型名 + hub="ms" 加载，靠 MODELSCOPE_CACHE 指到本地缓存，
    # 而不是把本地目录直接塞给 model 参数（那样会报 model ... is not registered）。
    os.environ["MODELSCOPE_CACHE"] = MODEL_CACHE
    os.environ["MODELSCOPE_LOCAL_CACHE"] = MODEL_CACHE


def _mute_stdout():
    """把 fd 1 临时接到 fd 2，隔离引擎启动提示行（连 C 层 printf 都拦得住）。"""
    try:
        sys.stdout.flush()
        saved = os.dup(1)
        os.dup2(2, 1)
        return saved
    except Exception:
        return None


def _unmute_stdout(saved):
    if saved is None:
        return
    try:
        sys.stdout.flush()
        os.dup2(saved, 1)
        os.close(saved)
    except Exception:
        pass


def _find_ffmpeg():
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if os.path.exists(p):
            return p
    except Exception:
        pass
    bin_dir = CFG.get("bin_dir") or os.environ.get("VTT_BIN_DIR", "")
    if bin_dir:
        name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        cand = os.path.join(bin_dir, name)
        if os.path.exists(cand):
            return cand
    return "ffmpeg"


FFMPEG = _find_ffmpeg()


def extract_audio(src: str, wav: str):
    if not os.path.exists(FFMPEG):
        raise FileNotFoundError("ffmpeg 未找到: %s" % FFMPEG)
    subprocess.run(
        [FFMPEG, "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000", wav],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def build_model():
    from funasr import AutoModel
    return AutoModel(
        model="iic/SenseVoiceSmall",
        vad_model="fsmn-vad",
        vad_kwargs={"max_single_segment_time": 30000},
        hub="ms",             # 从 ModelScope 加载（模型缓存由 MODELSCOPE_CACHE 指定）
        disable_update=True,
        disable_pbar=True,
        device="cpu",
    )


def _fmt_ms(ms):
    ms = int(round(float(ms)))
    s = ms // 1000
    mm = s // 60
    ss = s % 60
    xx = (ms % 1000) // 10
    return "%02d:%02d.%02d" % (mm, ss, xx)


def _postprocess(text):
    from funasr.utils.postprocess_utils import rich_transcription_postprocess
    return rich_transcription_postprocess(text)


def main():
    args = sys.argv[1:]
    use_ts = "--timestamps" in args
    positional = [a for a in args if not a.startswith("--")]
    if len(positional) < 1:
        print("用法: transcribe.py <媒体路径> [--timestamps]", file=sys.stderr)
        sys.exit(1)
    src = positional[0]
    if not os.path.exists(src):
        print("文件不存在: %s" % src, file=sys.stderr)
        sys.exit(1)

    wav = os.path.join(tempfile.gettempdir(), "vtt_%d.wav" % os.getpid())
    saved = _mute_stdout()
    try:
        print("[1/2] ffmpeg 抽取音轨: %s" % os.path.basename(src), file=sys.stderr)
        extract_audio(src, wav)

        print("[2/2] FunASR 转写中(CPU)…" + ("带时间戳" if use_ts else ""), file=sys.stderr)
        model = build_model()

        if use_ts:
            res = model.generate(
                input=[wav],
                language="auto",   # 自动识别中英文
                use_itn=True,
                batch_size_s=60,
                merge_vad=True,
                merge_length_s=10,
                sentence_timestamp=True,
            )
        else:
            res = model.generate(
                input=[wav],
                language="auto",
                use_itn=True,
                batch_size_s=60,
                merge_vad=True,
                merge_length_s=15,
            )
    finally:
        _unmute_stdout(saved)
        if os.path.exists(wav):
            try:
                os.remove(wav)
            except Exception:
                pass

    if not res or not isinstance(res, list):
        print("（未识别到语音）")
        return

    r0 = res[0] or {}

    if use_ts:
        infos = r0.get("sentence_info") or []
        if infos:
            for seg in infos:
                start = seg.get("start", 0)
                end = seg.get("end", 0)
                text = _postprocess(seg.get("text") or "")
                if text:
                    print("[%s - %s] %s" % (_fmt_ms(start), _fmt_ms(end), text))
        else:
            text = _postprocess(r0.get("text") or "")
            if text:
                print("[00:00.00 - 00:00.00] %s" % text)
    else:
        print(_postprocess(r0.get("text") or ""))


if __name__ == "__main__":
    main()
