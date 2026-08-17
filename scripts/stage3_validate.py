# -*- coding: utf-8 -*-
"""Stage 3b: 视觉事件结构校验门禁（不过不进 Stage 4）。

检查 vlm 目录（或 v2_native 等版本目录）下 events_w*.json：
1. 完整性：每个窗口都有 events 文件；每条事件六字段齐全。
2. 时间轴：无空洞、无重叠、不越出窗口、不越出视频末尾；覆盖率 ≥99%。
3. 模板化：同一 evidence 全文重复 ≥5 次视为模板句；模板句占比 >10% 判不合格
   （兜底通道退化特征，详见 reference/首测报告_澳大利亚课.md 与四课核验报告）。
4. 段长：>20s 的段打印警告（v2 工艺上限 20s；10s 帧间隔下长段必漏窗内变迁）。
5. 词表：action 必须落在约定词表内（含 v2 新增"教师不可见"）。

用法：
  python scripts/stage3_validate.py --course <课程名>                 # 校验 <课程名>/vlm
  python scripts/stage3_validate.py --vlm-dir <课程名>/vlm/v2_native  # 校验版本目录
退出码 0=通过（可含警告），1=不合格。
"""
import argparse, json, re, sys
from collections import Counter
from pathlib import Path

ACTIONS = {"书写", "擦改", "展示", "指向", "播放", "使用电脑", "演示", "巡视",
           "观察", "站立讲述", "静默", "学生展示发言", "教师不可见"}
FIELDS = ("start", "end", "action", "location", "screen", "evidence")


def hms2s(t) -> float:
    parts = [float(x) for x in str(t).split(":")]
    return sum(v * m for v, m in zip(reversed(parts), [1, 60, 3600]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", help="课程目录（校验其 vlm/ 根目录）")
    ap.add_argument("--vlm-dir", help="直接指定含 windows.json 的 vlm 目录（如 vlm/v2_native）")
    ap.add_argument("--seg-warn", type=int, default=20, help="段长警告阈值秒")
    args = ap.parse_args()
    if args.vlm_dir:
        vdir = Path(args.vlm_dir)
    elif args.course:
        vdir = Path(args.course) / "vlm"
    else:
        ap.error("--course 或 --vlm-dir 必传一个")
    # 版本目录（如 v2_native）不带 windows.json 时，回退到父目录找
    win_file = vdir / "windows.json"
    if not win_file.exists() and (vdir.parent / "windows.json").exists():
        win_file = vdir.parent / "windows.json"
    if not win_file.exists():
        print(f"FAIL: 找不到 windows.json（{vdir} 及其父目录都没有）")
        return 1
    windows = json.loads(win_file.read_text(encoding="utf-8"))

    errors, warns = [], []
    ev_counter, act_counter = Counter(), Counter()
    n_events, covered = 0, 0.0
    long_segs = []

    for w in windows:
        wid = w["window_id"]
        f = vdir / f"events_w{wid:03d}.json"
        if not f.exists():
            errors.append(f"w{wid:03d}: events 文件缺失")
            continue
        try:
            evs = json.loads(f.read_text(encoding="utf-8"))
        except Exception as ex:
            errors.append(f"w{wid:03d}: JSON 解析失败 {ex}")
            continue
        if not evs:
            errors.append(f"w{wid:03d}: events 为空")
            continue
        n_events += len(evs)
        prev_end = w["start_sec"]
        for i, e in enumerate(evs):
            for k in FIELDS:
                if k not in e:
                    errors.append(f"w{wid:03d}[{i}]: 缺字段 {k}")
            act_counter[e.get("action", "?")] += 1
            ev_counter[e.get("evidence", "")] += 1
            s, en = hms2s(e["start"]), hms2s(e["end"])
            if s > prev_end:
                errors.append(f"w{wid:03d}: 空洞 {prev_end}-{s}s")
            if s < prev_end:
                errors.append(f"w{wid:03d}: 重叠 {e['start']} 起点 {s} < 前段末 {prev_end}")
            if en > w["end_sec"] + 0.5:
                errors.append(f"w{wid:03d}: 越界 {e['start']}-{e['end']}（窗口至 {w['end']}）")
            if en - s > args.seg_warn:
                long_segs.append(f"w{wid:03d} {e['start']}-{e['end']} ({int(en-s)}s)")
            if e.get("action") not in ACTIONS:
                warns.append(f"w{wid:03d}: action 不在词表 '{e.get('action')}'")
            covered += max(0, min(en, w["end_sec"]) - max(s, w["start_sec"]))
            prev_end = max(prev_end, en)
        if prev_end < w["end_sec"] - 0.5:
            errors.append(f"w{wid:03d}: 窗尾空洞 {prev_end}-{w['end_sec']}s")

    total = windows[-1]["end_sec"]
    cov = covered / total * 100 if total else 0
    boiler = sum(c for s, c in ev_counter.items() if c >= 5)
    boiler_ratio = boiler / n_events * 100 if n_events else 0
    if boiler_ratio > 10:
        errors.append(f"模板化超标: ≥5次重复的 evidence 占 {boiler}/{n_events} ({boiler_ratio:.0f}%) > 10%")
    if cov < 99:
        errors.append(f"覆盖率不足: {cov:.1f}% < 99%")
    if long_segs:
        warns.append(f">{args.seg_warn}s 长段 {len(long_segs)} 条（v2 工艺上限 {args.seg_warn}s）: "
                     + ", ".join(long_segs[:5]) + (" ..." if len(long_segs) > 5 else ""))

    print(f"== stage3_validate: {vdir}")
    print(f"窗口 {len(windows)} | 事件 {n_events} | 覆盖率 {cov:.1f}% | 模板化 {boiler_ratio:.0f}% | "
          f"结构错误 {len(errors)} | 警告 {len(warns)}")
    print("action 分布:", dict(act_counter.most_common()))
    for x in errors[:20]:
        print("  ERR ", x)
    if len(errors) > 20:
        print(f"  ERR  ... 共 {len(errors)} 条")
    for x in warns[:10]:
        print("  WARN", x)
    ok = not errors
    print("判定:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
