# -*- coding: utf-8 -*-
"""Stage 1: 本地 ASR 转录（复用 media-transcribe 的 QwenASREngine）。

课堂录音三项加固 + 进程隔离：
1. VAD 静音跳过——静音段送 LLM-ASR 会幻觉重复直至 GGML_ASSERT abort()；
2. temperature=0.0 贪心解码 + 15s chunk 降低重复失控概率；
3. 段级进程隔离——少数困难音频段（远场/噪音）仍会触发 llama.cpp abort()，
   Python except 接不住，所以用 调度器+worker 子进程：worker 崩了由调度器
   重启续跑，进度不前进则把当前段标记 failed 跳过（毒段先用小 chunk 重试一次）。

用法：
  python stage1_asr.py <audio> --out-dir transcripts           # 调度器模式
  python stage1_asr.py <audio> --out-dir transcripts --worker  # 内部用
产出：_progress.json（逐段进度+字级时间戳）+ 合并 <名>_timed.md/.json/.txt
"""
import argparse, json, os, subprocess, sys, time, re
from pathlib import Path

# 外部依赖路径：环境变量优先，默认按常规安装位置解析
#   MEDIA_TRANSCRIBE_SCRIPTS — media-transcribe skill 的 scripts 目录（提供 oral_pipeline）
#   QWEN_ASR_ENGINE_DIR      — qwen_asr_gguf 引擎目录（含 qwen_asr_gguf 包与 chinese_itn shim）
#   QWEN_ASR_MODEL_DIR       — qwen3-asr-gguf 模型权重目录
MT = Path(os.environ.get("MEDIA_TRANSCRIBE_SCRIPTS",
                         Path.home() / ".agents" / "skills" / "media-transcribe" / "scripts"))
ENGINE_DIR = Path(os.environ.get("QWEN_ASR_ENGINE_DIR",
                                 Path.home() / "models" / "qwen3-asr-gguf" / "engine"))
MODEL_DIR = Path(os.environ.get("QWEN_ASR_MODEL_DIR",
                                Path.home() / "models" / "qwen3-asr-gguf" / "model"))

sys.path.insert(0, str(MT))
sys.path.insert(0, str(ENGINE_DIR))
ORIG_CWD = Path.cwd()
for _p, _env in ((MT, "MEDIA_TRANSCRIBE_SCRIPTS"), (ENGINE_DIR, "QWEN_ASR_ENGINE_DIR"),
                 (MODEL_DIR, "QWEN_ASR_MODEL_DIR")):
    if not _p.is_dir():
        raise SystemExit(f"依赖目录不存在: {_p}\n请安装对应组件或用环境变量 {_env} 指定路径")
os.chdir(ENGINE_DIR)  # 保证 qwen_asr_gguf 包与 chinese_itn shim 可导入


def detect_speech_segments(audio: Path, total: float, noise_db: float = -38.0,
                           min_speech: float = 0.8, min_gap: float = 1.2,
                           pad: float = 0.5, max_seg: float = 240.0):
    r = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(audio), "-af",
         f"silencedetect=noise={noise_db}dB:d={min_gap}", "-f", "null", "-"],
        capture_output=True, text=True)
    sil, sil_pos = [], 0
    while True:
        m = re.search(r"silence_start: ([\d.]+)", r.stderr[sil_pos:])
        if not m:
            break
        s = float(m.group(1)); sil_pos += m.end()
        em = re.search(r"silence_end: ([\d.]+)", r.stderr[sil_pos:])
        e = float(em.group(1)) if em else total
        sil.append((s, e))
    speech, cur = [], 0.0
    for s, e in sil:
        if s - cur >= min_speech:
            speech.append([max(0.0, cur - pad), min(total, s + pad)])
        cur = max(cur, e)
    if total - cur >= min_speech:
        speech.append([max(0.0, cur - pad), total])
    out = []
    for s, e in speech:
        while e - s > max_seg:
            out.append((s, s + max_seg)); s += max_seg
        if e - s > 0:
            out.append((s, e))
    return out


def load_progress(prog: Path):
    if prog.exists():
        try:
            ds = json.loads(prog.read_text(encoding="utf-8"))
            for d in ds:
                if "status" not in d:  # 旧版格式迁移
                    d["status"] = "ok"
            return {d["seg_start"]: d for d in ds}
        except Exception:
            return {}
    return {}


def save_progress(prog: Path, done: dict):
    tmp = prog.with_suffix(".tmp")
    tmp.write_text(json.dumps(list(done.values()), ensure_ascii=False), encoding="utf-8")
    tmp.replace(prog)


def run_worker(audio, out_dir, chunk_size, temperature):
    return subprocess.run(
        [sys.executable, __file__, str(audio), "--out-dir", str(out_dir),
         "--worker", "--chunk-size", str(chunk_size), "--temperature", str(temperature)],
        cwd=str(ORIG_CWD), capture_output=True, text=True)


def worker(args, audio, out_dir, segs, prog):
    from oral_pipeline import build_config
    from qwen_asr_gguf.inference import QwenASREngine, exporters

    done = load_progress(prog)
    cfg = build_config(str(MODEL_DIR), args.provider, args.n_ctx, args.chunk_size,
                       memory_num=1, timestamp=True)
    engine = QwenASREngine(config=cfg)
    try:
        for s, e in segs:
            if s in done or done.get(s, {}).get("status") in ("ok", "failed"):
                continue
            base = out_dir / f"{Path(audio).stem}__{s:08.2f}".replace(".", "_")
            res = engine.transcribe(audio_file=str(audio), language="Chinese", context="",
                                    start_second=s, duration=e - s,
                                    temperature=args.temperature)
            plain = res.text.strip()
            items = ([{"text": it.text, "start": it.start_time, "end": it.end_time}
                      for it in res.alignment] if res.alignment else [])
            if items and items[-1]["end"] < s + 1:  # 相对切片 -> 全局偏移
                for it in items:
                    it["start"] += s; it["end"] += s
            exporters.export_to_json(f"{base}.json", res)
            done[s] = {"seg_start": s, "seg_end": e, "status": "ok", "chars": len(plain),
                       "items": items, "text": plain}
            save_progress(prog, done)
            print(f"  seg {s:7.1f}-{e:7.1f}s: {len(plain):4d}字 {len(items):4d}ts {plain[:30]!r}",
                  flush=True)
    finally:
        engine.shutdown()


def merge_outputs(audio, out_dir, prog, done):
    from oral_pipeline import split_sentences, write_timed_md
    segs_done = sorted((d for d in done.values() if d["status"] == "ok"),
                       key=lambda d: d["seg_start"])
    all_items = sorted((it for d in segs_done for it in d["items"]), key=lambda x: x["start"])
    all_text = "".join(d["text"] for d in segs_done)
    sents = split_sentences(all_items)
    stem = out_dir / Path(audio).stem
    write_timed_md(f"{stem}_timed.md", Path(audio).name,
                   all_items[-1]["end"] if all_items else 0.0, sents, all_text)
    Path(f"{stem}.json").write_text(
        json.dumps([{"text": it["text"], "start": it["start"], "end": it["end"]}
                    for it in all_items], ensure_ascii=False), encoding="utf-8")
    Path(f"{stem}.txt").write_text(all_text, encoding="utf-8")
    n_fail = sum(1 for d in done.values() if d["status"] == "failed")
    print(f"合并: {len(all_items)} 时间戳 {len(all_text)} 字 ({n_fail} 毒段跳过) "
          f"-> {stem}_timed.md/.json/.txt")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--chunk-size", type=float, default=15.0)
    ap.add_argument("--n-ctx", type=int, default=2048)
    ap.add_argument("--provider", default="DML")
    ap.add_argument("--noise-db", type=float, default=-38.0)
    ap.add_argument("--worker", action="store_true", help="内部：单进程转所有未完成段")
    ap.add_argument("--patch", nargs=2, type=float, metavar=("START", "END"),
                    help="补转指定秒区间（不做 VAD），合入进度后重合并")
    args = ap.parse_args()

    audio = (ORIG_CWD / args.audio).resolve() if not Path(args.audio).is_absolute() else Path(args.audio)
    out_dir_arg = Path(args.out_dir)
    out_dir = (out_dir_arg if out_dir_arg.is_absolute() else ORIG_CWD / out_dir_arg).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    prog = out_dir / "_progress.json"

    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(audio)],
        capture_output=True, text=True).stdout.strip())

    if args.patch:
        from oral_pipeline import build_config
        from qwen_asr_gguf.inference import QwenASREngine, exporters
        ps, pe = args.patch
        cfg = build_config(str(MODEL_DIR), args.provider, args.n_ctx, args.chunk_size,
                           memory_num=1, timestamp=True)
        engine = QwenASREngine(config=cfg)
        try:
            base = out_dir / f"{Path(audio).stem}__{ps:08.2f}".replace(".", "_")
            res = engine.transcribe(audio_file=str(audio), language="Chinese", context="",
                                    start_second=ps, duration=pe - ps,
                                    temperature=args.temperature)
            plain = res.text.strip()
            items = ([{"text": it.text, "start": it.start_time, "end": it.end_time}
                      for it in res.alignment] if res.alignment else [])
            if items and items[-1]["end"] < ps + 1:  # 相对切片 -> 全局偏移
                for it in items:
                    it["start"] += ps; it["end"] += ps
            exporters.export_to_json(f"{base}.json", res)
            done = load_progress(prog)
            done[ps] = {"seg_start": ps, "seg_end": pe, "status": "ok",
                        "chars": len(plain), "items": items, "text": plain}
            save_progress(prog, done)
            print(f"patch {ps}-{pe}s: {len(plain)}字 {plain[:80]!r}")
        finally:
            engine.shutdown()
        merge_outputs(audio, out_dir, prog, load_progress(prog))
        return

    segs = detect_speech_segments(audio, dur, noise_db=args.noise_db)
    print(f"音频 {dur:.0f}s -> {len(segs)} 有声段 (语音 {sum(e-s for s,e in segs):.0f}s)", flush=True)

    if args.worker:
        worker(args, audio, out_dir, segs, prog)
        return

    # ---- 调度器：循环拉起 worker，无进展则毒段重试+跳过 ----
    attempts, t_all = {}, time.time()
    while True:
        done = load_progress(prog)
        todo = [s for s, e in segs if done.get(s, {}).get("status") not in ("ok", "failed")]
        if not todo:
            break
        n_before = sum(1 for d in done.values() if d["status"] == "ok")
        print(f"[调度] 未完成 {len(todo)} 段, 启动 worker...", flush=True)
        r = run_worker(audio, out_dir, args.chunk_size, args.temperature)
        done = load_progress(prog)
        n_after = sum(1 for d in done.values() if d["status"] == "ok")
        if n_after == n_before:  # worker 起手就崩/无进展 -> 处理下一段
            s = todo[0]
            attempts[s] = attempts.get(s, 0) + 1
            if attempts[s] == 1:  # 毒段先小 chunk 重试一次
                print(f"[调度] 段 {s:.1f}s 疑似毒段, 小chunk重试", flush=True)
                r2 = run_worker(audio, out_dir, 8.0, args.temperature)
                done2 = load_progress(prog)
                if done2.get(s, {}).get("status") == "ok":
                    continue
            # 重试也崩 -> 标记 failed 跳过
            done = load_progress(prog)
            e = next(e2 for s2, e2 in segs if s2 == s)
            done[s] = {"seg_start": s, "seg_end": e, "status": "failed", "chars": 0,
                       "items": [], "text": ""}
            save_progress(prog, done)
            print(f"[调度] 段 {s:.1f}s 标记 failed 跳过", flush=True)

    done = load_progress(prog)
    merge_outputs(audio, out_dir, prog, done)
    print(f"总耗时 {time.time()-t_all:.0f}s")


if __name__ == "__main__":
    main()
