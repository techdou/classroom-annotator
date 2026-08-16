# -*- coding: utf-8 -*-
"""Stage 4a: 组装"操作级标注任务包"（agent 推理用）。

输入：transcripts/<名>.json（字级时间戳）、vlm/events_w*.json（视觉事件）
输出：llm/tasks/seg_XX.md —— 每份任务包含一个 5 分钟段的逐句转录 + 视觉事件，
      以及该段的 ASR/视觉双时间线合并视图，供 agent 逐段推理操作级标注。
"""
import argparse, json
from pathlib import Path

OP_RULES = """## 操作类型速查（11 类）
书写WR: 黑板/白板/PPT批注区/纸质材料上新增文字、符号、图形（新增！擦除已有内容是擦改EC）
擦改EC: 对已有内容擦除/删除/覆盖/订正（PPT翻页不是擦改，是使用OP）
展示DP: 向学生呈现某材料/页面/实物使其成为观察对象（"看这里""观察这张图"；PPT持续显示≠展示）
指向PT: 手/教鞭/激光笔有明确方向性地指向具体位置（单纯站在屏幕旁不是指向）
播放PL: 启动/暂停/继续/停止视频、音频、自动动画（点击翻页是使用；静态图呈现是展示）
使用OP: 对设备/课件/平台点击、切换、拖动、缩放等控制性操作（翻页优先识别为使用）
演示DE: 实际操作实物/教具/实验器材/动态软件呈现过程或变化
巡视PA: 走入学生座位区，走动、停留、低头查看（讲台↔黑板间移动不是巡视）
观察OB: 原地静止地看/听/等待学生回答/展示/讨论（走动查看是巡视；听完后评价是讲述）
讲述TL: 口头朗读/说明/解释/归纳/总结/评价，不要求学生立即回答（自问自答仍算讲述）
提问QN: 提出问题并要求学生思考/回答（"为什么""谁来说""对不对"；追问也算提问）

## 工具速查
口头语言OL | 板书BW | 纸质材料-教材PB | 纸质材料-任务单PM | 其他纸质材料PO | PPT | 白板内置软件工具WS | 外部学科教学软件SS | 实验器材EE | 实物投影PP | 教具TA | 其他OT
言语类操作主工具=口头语言；围绕PPT讲述可写"口头语言；PPT"。教师在黑板上写字→书写+板书。

## 切分粒度（按金标准实际校准，不抠1-10秒）
中位15秒左右，短可2-3秒，长可达1分钟；同一动作持续很久且目的不变时保持一条。
讲述长段（>40s）若内容主题切换明显，可拆分。每条操作只标一个主要操作类型。

## 输出 JSON 格式（每条操作）
{"start":秒,"end":秒,"op_type":"讲述","tool":"口头语言","org":"教师面向全体学生","org_code":"T-S_whole","evidence_v":"画面证据","evidence_r":"对应原话(言语类必填)","note":""}
org/org_code 可选值: 教师面向全体学生 T-S_whole | 个体学生面向小组 T-S_whole(小组内时写"学生面向小组") | 师生互动 T-S_ind
注意: 学生回答/汇报段也是操作（金标准把学生汇报标为"讲述"或"观察"由教师行为决定——教师听学生答=观察OB；学生发言本身作为该操作的证据R记录）。
"""


def fmt_ts(t: float) -> str:
    t = max(0, int(t))
    return f"{t//3600:02d}:{t%3600//60:02d}:{t%60:02d}"


def load_sentences(asr_json: Path):
    items = json.loads(asr_json.read_text(encoding="utf-8"))
    # 字级 → 按 2.5s 静音或标点聚合为"句"
    sents, cur = [], []
    for it in items:
        if cur and it["start"] - cur[-1]["end"] > 2.0:
            sents.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                          "text": "".join(x["text"] for x in cur)})
            cur = []
        cur.append(it)
    if cur:
        sents.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                      "text": "".join(x["text"] for x in cur)})
    # 句内再按长句切（>80字强切）
    out = []
    for s in sents:
        t = s["text"]
        while len(t) > 120:
            out.append({"start": s["start"], "end": s["end"], "text": t[:120]})
            t = t[120:]
        if t:
            out.append(s | {"text": t})
    return out


def load_visual_events(vlm_dir: Path):
    evs = []
    for f in sorted(vlm_dir.glob("events_w*.json")):
        try:
            for e in json.loads(f.read_text(encoding="utf-8")):
                if not isinstance(e, dict) or "start" not in e:
                    continue
                evs.append(e)
        except Exception as ex:
            print(f"warn: {f.name} 解析失败 {ex}")
    def sec(ts):
        p = [float(x) for x in str(ts).split(":")]
        return sum(v * m for v, m in zip(reversed(p), [1, 60, 3600]))
    for e in evs:
        e["_s"], e["_e"] = sec(e.get("start", 0)), sec(e.get("end", 0))
    return sorted(evs, key=lambda x: x["_s"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", required=True, help="课程目录")
    ap.add_argument("--seg-min", type=int, default=5, help="任务包段长(分钟)")
    args = ap.parse_args()
    cdir = Path(args.course)
    cands = [p for p in (cdir / "transcripts").glob("*.json")
             if p.name != "_progress.json" and "__" not in p.stem]
    asr_json = max(cands, key=lambda p: p.stat().st_size)
    sents = load_sentences(asr_json)
    evs = load_visual_events(cdir / "vlm")
    dur = max(sents[-1]["end"], evs[-1]["_e"] if evs else 0) if sents else 0
    seg_len = args.seg_min * 60
    n_seg = int(dur // seg_len) + 1
    tdir = cdir / "llm" / "tasks"; tdir.mkdir(parents=True, exist_ok=True)

    for i in range(n_seg):
        s0, s1 = i * seg_len, (i + 1) * seg_len
        ss = [x for x in sents if x["start"] < s1 and x["end"] > s0]
        ee = [x for x in evs if x["_s"] < s1 and x["_e"] > s0]
        lines = [f"# 任务包 seg_{i+1:02d}（{fmt_ts(s0)} - {fmt_ts(min(s1,dur))}）",
                 "", "## A. 视觉事件时间线（VLM 看帧结果，时间有±10s误差）", ""]
        for e in ee:
            lines.append(f"- [{fmt_ts(e['_s'])}-{fmt_ts(e['_e'])}] {e.get('action','')} | "
                         f"位置:{e.get('location','')} | 屏幕黑板:{e.get('screen','')} | "
                         f"证据:{e.get('evidence','')}")
        lines += ["", "## B. 逐句转录（字级时间戳聚合，秒）", ""]
        for x in ss:
            lines.append(f"[{x['start']:.1f}-{x['end']:.1f}] {x['text']}")
        lines += ["", "---", OP_RULES,
                  f"请基于 A(视觉)+B(转录) 推理本段（{fmt_ts(s0)}-{fmt_ts(min(s1,dur))}）的操作级标注。",
                  "以转录句边界为主轴切分操作片段（讲述/提问），视觉事件用于插入/校正视觉类操作（书写/指向/使用/巡视等）。",
                  "两条时间线要融合成一条不重叠的时间轴。输出纯 JSON 数组，每条一个对象，按 start 升序。"]
        (tdir / f"seg_{i+1:02d}.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"OK: {n_seg} 个任务包 -> {tdir}（句子 {len(sents)}，视觉事件 {len(evs)}，时长 {dur:.0f}s）")


if __name__ == "__main__":
    main()
