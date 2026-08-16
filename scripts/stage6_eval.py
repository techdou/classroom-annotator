# -*- coding: utf-8 -*-
"""Stage 6: 自动预标注 vs 金标准对齐评测。

指标：
- 覆盖率：金标准条目被任一自动条目覆盖(重叠>1s)的比例
- 边界 IoU：每条金标准取重叠最大的自动条目，算时间 IoU（1=完全重合）
- 类型准确率：对齐对中操作类型/行为类型/活动类型一致的比例
- 数量比：自动条数/金标准条数（理想≈1）
输出：draft/eval_report.md + 控制台摘要
"""
import argparse, json
from pathlib import Path
import pandas as pd


def sec(t):
    if isinstance(t, str):
        p = [int(float(x)) for x in t.split(":")]
        return sum(v * m for v, m in zip(reversed(p), [1, 60, 3600]))
    if hasattr(t, "hour"):
        return t.hour * 3600 + t.minute * 60 + t.second
    return float(t)


def iou(a0, a1, b0, b1):
    inter = max(0, min(a1, b1) - max(a0, b0))
    union = max(a1, b1) - min(a0, b0)
    return inter / union if union > 0 else 0.0


def align(gold, auto, type_col):
    """gold/auto: [{_s,_e,_type,...}] -> 对齐结果列表"""
    res = []
    for g in gold:
        best, best_i = None, 0.0
        for a in auto:
            ov = min(g["_e"], a["_e"]) - max(g["_s"], a["_s"])
            i = iou(g["_s"], g["_e"], a["_s"], a["_e"])
            if ov > 1 and i > best_i:
                best, best_i = a, i
        res.append({"gold": g, "auto": best, "iou": best_i,
                    "type_match": bool(best) and str(g[type_col]).strip() == str(best.get(type_col, "")).strip()})
    return res


def norm_type(v):
    """金标准活动类型带编码后缀，如'新知学习（NK）'。"""
    s = str(v).strip()
    return s.split("（")[0].split("(")[0]


def report(name, gold, auto, type_col, type_norm=None):
    res = align(gold, auto, type_col)
    n = len(gold)
    covered = sum(1 for r in res if r["auto"])
    matched = sum(1 for r in res if r["type_match"])
    ious = [r["iou"] for r in res if r["auto"]]
    bad = [(r["iou"], r["gold"], r["auto"]) for r in res if r["auto"] and not r["type_match"]]
    lines = [f"## {name}", f"- 金标准 {n} 条 vs 自动 {len(auto)} 条 (数量比 {len(auto)/max(n,1):.2f})",
             f"- 覆盖率 {covered}/{n} = {covered/n*100:.0f}%",
             f"- 边界IoU 均值 {sum(ious)/max(len(ious),1):.3f}，中位 {sorted(ious)[len(ious)//2] if ious else 0:.3f}",
             f"- 类型一致 {matched}/{covered} = {matched/max(covered,1)*100:.0f}%"]
    if bad:
        lines.append(f"- 类型不一致 {len(bad)} 条（前10）：")
        for i, g, a in sorted(bad, key=lambda x: -x[0])[:10]:
            lines.append(f"  - [{sec2ts(g['_s'])}] 金:{g.get(type_col)} vs 自:{a.get(type_col)} (IoU {i:.2f})")
    return "\n".join(lines), res


def sec2ts(s):
    s = int(s)
    return f"{s//3600:02d}:{s%3600//60:02d}:{s%60:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", required=True)
    ap.add_argument("--gold", required=True, help="金标准 xlsx")
    args = ap.parse_args()
    cdir = Path(args.course)
    draft_x = next((cdir / "draft").glob("*_预标注.xlsx"))

    out = []
    sheet_cfg = [("操作", "操作类型"), ("行为", "行为类型"), ("活动", "活动类型")]
    details = {}
    for sheet, tcol in sheet_cfg:
        g = pd.read_excel(args.gold, sheet_name=sheet)
        a = pd.read_excel(draft_x, sheet_name=sheet)
        s0c = next(c for c in g.columns if "开始" in str(c))
        e0c = next(c for c in g.columns if "结束" in str(c))
        idc = g.columns[0]
        gold = [{"_s": sec(r[s0c]), "_e": sec(r[e0c]), "_id": str(r[idc]),
                 tcol: norm_type(r[tcol])} for _, r in g.iterrows()]
        auto = [{"_s": sec(r[s0c]), "_e": sec(r[e0c]), "_id": str(r[idc]),
                 tcol: norm_type(r[tcol]), "_tool": str(r["工具"]) if "工具" in a.columns else ""}
                for _, r in a.iterrows()]
        txt, res = report(f"{sheet}级", gold, auto, tcol)
        out.append(txt); details[sheet] = res

    # 工具一致率（操作级对齐对里）
    gdf = pd.read_excel(args.gold, sheet_name="操作")
    adf = pd.read_excel(draft_x, sheet_name="操作")
    gtool = {str(r[gdf.columns[0]]): str(r["工具"]) for _, r in gdf.iterrows()}
    res_op = details["操作"]
    tool_match = sum(1 for r in res_op
                     if r["auto"] and r["gold"]["_id"] in gtool
                     and gtool[r["gold"]["_id"]].split("；")[0] == str(r["auto"].get("_tool", "")).split("；")[0])
    out.append(f"## 工具一致率（操作级对齐对）\n- {tool_match}/{sum(1 for r in res_op if r['auto'])}")

    rpt = cdir / "draft" / "eval_report.md"
    rpt.write_text("# 评测报告：自动预标注 vs 金标准\n\n" + "\n\n".join(out), encoding="utf-8")
    print("\n\n".join(out))
    print(f"\n-> {rpt}")


if __name__ == "__main__":
    main()
