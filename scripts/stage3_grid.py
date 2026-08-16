# -*- coding: utf-8 -*-
"""Stage 3a: 把 2s 抽帧按窗口拼网格图，供 VLM 批量看帧。

每窗口默认 60s、取 6 帧（10s 间隔），拼 2x3 网格，每帧左上角烧录时间戳。
输出 vlm/grid_w%03d.jpg + vlm/windows.json。
"""
import argparse, json, re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FONT = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 26) if Path("C:/Windows/Fonts/arialbd.ttf").exists() else ImageFont.load_default()

def fmt(sec):
    return f"{sec//3600:02d}:{sec%3600//60:02d}:{sec%60:02d}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True, help="frames 目录")
    ap.add_argument("--out", required=True, help="vlm 输出目录")
    ap.add_argument("--step", type=int, default=2, help="抽帧间隔秒")
    ap.add_argument("--window", type=int, default=60, help="窗口秒数")
    ap.add_argument("--per-window", type=int, default=6, help="每窗口帧数")
    ap.add_argument("--thumb-w", type=int, default=640)
    args = ap.parse_args()

    frames_dir, out_dir = Path(args.frames), Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(frames_dir.glob("frame2s_*.jpg"))
    if not files:
        raise SystemExit("frames 目录没有 frame2s_*.jpg")
    total_sec = len(files) * args.step
    interval = args.window // args.per_window

    cols, rows = 3, 2
    windows = []
    w_start = 0
    idx = 0
    while w_start < total_sec:
        w_end = min(w_start + args.window, total_sec)
        picks = []
        for k in range(args.per_window):
            t = w_start + k * interval
            if t >= w_end and k == args.per_window - 1:
                t = w_end - args.step
            fn = f"frame2s_{t // args.step + 1:05d}.jpg"
            fp = frames_dir / fn
            if fp.exists():
                picks.append((t, fn))
        if not picks:
            w_start += args.window
            continue
        th_h = round(args.thumb_w * 9 / 16)
        grid = Image.new("RGB", (cols * args.thumb_w, rows * th_h), (16, 16, 16))
        for i, (t, fn) in enumerate(picks):
            im = Image.open(frames_dir / fn).resize((args.thumb_w, th_h))
            d = ImageDraw.Draw(im)
            label = fmt(t)
            tw = d.textlength(label, font=FONT)
            d.rectangle([6, 6, 12 + tw, 42], fill=(0, 0, 0))
            d.text((10, 8), label, fill=(255, 220, 0), font=FONT)
            grid.paste(im, ((i % cols) * args.thumb_w, (i // cols) * th_h))
        idx += 1
        grid_path = out_dir / f"grid_w{idx:03d}.jpg"
        grid.save(grid_path, quality=82)
        windows.append({
            "window_id": idx, "start": fmt(w_start), "end": fmt(w_end),
            "start_sec": w_start, "end_sec": w_end,
            "grid": grid_path.name,
            "frame_times": [{"sec": t, "ts": fmt(t), "file": fn} for t, fn in picks],
            "vlm_events": None,
        })
        w_start += args.window

    (out_dir / "windows.json").write_text(json.dumps(windows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK: {idx} 个窗口 -> {out_dir}\\grid_w*.jpg + windows.json ({total_sec}s 视频, 帧间隔 {interval}s)")

if __name__ == "__main__":
    main()
