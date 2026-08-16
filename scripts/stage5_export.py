# -*- coding: utf-8 -*-
"""Stage 5: 三级标注 JSON -> 与金标准同构的三 sheet xlsx + 校对 HTML。

输入约定：llm/operations.json（每条含 behavior_id）、behaviors.json（id+activity_id）、
activities.json（id），编号已是最终编号（M00001/B0001/A0001）。
输出：draft/<课程名>_预标注.xlsx、draft/review.html（点时间戳跳视频）
"""
import argparse, json, html
from pathlib import Path
import pandas as pd

OP_COLS = ["操作编号", "所属行为", "开始时间", "结束时间", "图片帧", "组织方式", "组织方式编码",
           "操作类型", "操作编码", "工具", "工具编码", "证据来源-V", "证据来源-R", "备注"]
BE_COLS = ["行为编号", "所属活动", "开始时间", "结束时间", "图片帧", "行为类型", "行为类型编码",
           "主体", "主体编码", "客体描述（行为目的）", "客体编码", "操作-工具序列", "操作-工具序列编码",
           "共同体（互动范围）", "共同体编码", "证据来源-V", "证据来源-R", "备注"]
AC_COLS = ["编号", "开始时间", "结束时间", "图片帧", "活动类型", "核心任务或目标", "主体", "主体编码",
           "客体描述（活动目的）", "客体编码", "行为序列", "组织方式", "组织方式编码",
           "证据说明", "证据来源-V", "证据来源-R", "备注"]

OP_CODE = {"书写": "WR", "擦改": "EC", "展示": "DP", "指向": "PT", "播放": "PL", "使用": "OP",
           "演示": "DE", "巡视": "PA", "观察": "OB", "讲述": "TL", "提问": "QN"}
TOOL_CODE = {"口头语言": "OL", "板书": "BW", "纸质材料-教材": "PB", "纸质材料-任务单": "PM",
             "其他纸质材料": "PO", "PPT": "PPT", "白板内置软件工具": "WS", "外部调用的学科教学软件工具": "SS",
             "实验器材": "EE", "实物投影": "PP", "教具": "TA", "其他": "OT"}
BE_CODE = {"引起注意": "AA", "告知学习目标": "EO", "刺激回忆旧知": "SR", "呈现刺激材料": "SP",
           "提供学习指导": "PG", "诱发学习行为/引出行为": "IB", "提供反馈": "PF",
           "检测学习结果/评价行为": "EP", "促进保持和迁移": "RT"}
AC_CODE = {"新知学习": "NK", "评价": "EL", "总结迁移": "SF"}


def ts(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec//3600:02d}:{sec%3600//60:02d}:{sec%60:02d}"


def frame_ref(d, step=2):
    mid = (d["start"] + d["end"]) / 2
    return f"frame2s_{int(mid // step) + 1:05d}.jpg ({ts(mid)})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", required=True)
    ap.add_argument("--video", default="", help="视频路径（校对页跳转用，默认 ../课程名.mp4）")
    args = ap.parse_args()
    cdir = Path(args.course)
    ldir = cdir / "llm"
    ops = json.loads((ldir / "operations.json").read_text(encoding="utf-8"))
    bes = json.loads((ldir / "behaviors.json").read_text(encoding="utf-8"))
    acs = json.loads((ldir / "activities.json").read_text(encoding="utf-8"))
    ops.sort(key=lambda x: x["start"]); bes.sort(key=lambda x: x["start"]); acs.sort(key=lambda x: x["start"])

    ops_of_be = {}
    for m in ops:
        ops_of_be.setdefault(m.get("behavior_id", ""), []).append(m)

    op_rows = []
    for i, m in enumerate(ops, 1):
        m["id"] = f"M{i:05d}"
        tool = m.get("tool", "其他").split("；")[0].split(";")[0].strip()
        op_rows.append({
            "操作编号": m["id"], "所属行为": m.get("behavior_id", ""),
            "开始时间": ts(m["start"]), "结束时间": ts(m["end"]), "图片帧": frame_ref(m),
            "组织方式": m.get("org", "教师面向全体学生"),
            "组织方式编码": m.get("org_code", "T-S_whole"),
            "操作类型": m["op_type"], "操作编码": OP_CODE.get(m["op_type"], ""),
            "工具": m.get("tool", tool), "工具编码": TOOL_CODE.get(tool, "OT"),
            "证据来源-V": m.get("evidence_v", ""), "证据来源-R": m.get("evidence_r", ""),
            "备注": m.get("note", "")})

    be_rows = []
    for i, b in enumerate(bes, 1):
        b["id"] = f"B{i:04d}"
        ms = sorted(ops_of_be.get(b.get("_key", b["id"]), []), key=lambda x: x["start"])
        seq = [f"{m['id']}[{m['op_type']}-{m.get('tool','其他').split('；')[0]}]" for m in ms]
        seqc = [f"{m['id']}[{OP_CODE.get(m['op_type'],'')}-{TOOL_CODE.get(m.get('tool','其他').split('；')[0],'OT')}]"
                for m in ms]
        ev_r = "；".join(m.get("evidence_r", "") for m in ms if m.get("evidence_r"))
        ev_v = "；".join(dict.fromkeys(m.get("evidence_v", "") for m in ms if m.get("evidence_v")))
        be_rows.append({
            "行为编号": b["id"], "所属活动": b.get("activity_id", ""),
            "开始时间": ts(b["start"]), "结束时间": ts(b["end"]), "图片帧": frame_ref(b),
            "行为类型": b["behavior_type"], "行为类型编码": BE_CODE.get(b["behavior_type"], ""),
            "主体": b.get("subject", "师生共同体"), "主体编码": b.get("subject_code", "T&S"),
            "客体描述（行为目的）": b.get("object_desc", ""), "客体编码": b.get("object_code", ""),
            "操作-工具序列": "；".join(seq), "操作-工具序列编码": "；".join(seqc),
            "共同体（互动范围）": b.get("community", "师生共同体-全班学生"),
            "共同体编码": b.get("community_code", "T&S-S_whole"),
            "证据来源-V": ev_v or b.get("evidence_v", ""), "证据来源-R": ev_r or b.get("evidence_r", ""),
            "备注": b.get("note", "")})
        for m in ops_of_be.get(b.get("_key", ""), []):
            m["behavior_id"] = b["id"]
    for row, m in zip(op_rows, ops):  # 回填最终行为编号
        row["所属行为"] = m.get("behavior_id", "")

    bes_of_ac = {}
    for b in bes:
        bes_of_ac.setdefault(b.get("activity_id", ""), []).append(b)
    ac_rows = []
    for i, a in enumerate(acs, 1):
        a["id"] = f"A{i:04d}"
        bs = sorted(bes_of_ac.get(a.get("_key", a["id"]), []), key=lambda x: x["start"])
        bseq = f"{bs[0]['id']}-{bs[-1]['id']}" if len(bs) > 1 else (bs[0]["id"] if bs else "")
        ac_rows.append({
            "编号": a["id"], "开始时间": ts(a["start"]), "结束时间": ts(a["end"]), "图片帧": frame_ref(a),
            "活动类型": a["activity_type"], "核心任务或目标": a.get("core_task", ""),
            "主体": a.get("subject", "师生共同体"), "主体编码": a.get("subject_code", "T&S"),
            "客体描述（活动目的）": a.get("object_desc", ""), "客体编码": a.get("object_code", ""),
            "行为序列": bseq, "组织方式": a.get("org", "教师面向全班"),
            "组织方式编码": a.get("org_code", "T-S_whole"),
            "证据说明": a.get("evidence_note", ""),
            "证据来源-V": a.get("evidence_v", ""), "证据来源-R": a.get("evidence_r", ""),
            "备注": a.get("note", "")})
        for b in bes_of_ac.get(a.get("_key", ""), []):
            b["activity_id"] = a["id"]
    for row, b in zip(be_rows, bes):
        row["所属活动"] = b.get("activity_id", "")

    draft = cdir / "draft"; draft.mkdir(exist_ok=True)
    name = cdir.name + "_预标注.xlsx"
    with pd.ExcelWriter(draft / name, engine="openpyxl") as w:
        pd.DataFrame(ac_rows, columns=AC_COLS).to_excel(w, sheet_name="活动", index=False)
        pd.DataFrame(be_rows, columns=BE_COLS).to_excel(w, sheet_name="行为", index=False)
        pd.DataFrame(op_rows, columns=OP_COLS).to_excel(w, sheet_name="操作", index=False)

    vid = args.video or f"../{cdir.name}.mp4"
    rows_html = "".join(
        f"<tr><td>{r['操作编号']}</td><td>{r['所属行为']}</td>"
        f"<td class='ts' data-s='{r['开始时间']}'>{r['开始时间']}</td><td>{r['结束时间']}</td>"
        f"<td>{r['操作类型']}({r['操作编码']})</td><td>{html.escape(str(r['工具']))}</td>"
        f"<td>{html.escape(str(r['证据来源-V']))}</td><td>{html.escape(str(r['证据来源-R']))}</td></tr>"
        for r in op_rows)
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>预标注校对 - {cdir.name}</title>
<style>body{{font-family:sans-serif;margin:16px}}table{{border-collapse:collapse;font-size:13px}}
td,th{{border:1px solid #ccc;padding:4px 8px;max-width:360px}}th{{background:#f0f0f0;position:sticky;top:0}}
.ts{{cursor:pointer;color:#06c}}video{{position:sticky;top:32px;width:480px;z-index:9}}</style></head><body>
<h3>{cdir.name} 操作级预标注（{len(op_rows)} 操作 / {len(be_rows)} 行为 / {len(ac_rows)} 活动）</h3>
<video id="v" controls src="{vid}"></video>
<p>点击"开始时间"跳转视频。</p>
<table><tr><th>编号</th><th>行为</th><th>开始</th><th>结束</th><th>操作</th><th>工具</th><th>证据V</th><th>证据R</th></tr>
{rows_html}</table>
<script>document.querySelectorAll('.ts').forEach(td=>td.onclick=()=>{{
const p=td.dataset.s.split(':').map(Number);const v=document.getElementById('v');
v.currentTime=p[0]*3600+p[1]*60+p[2];v.play();window.scrollTo({{top:0}});}});</script></body></html>"""
    (draft / "review.html").write_text(page, encoding="utf-8")
    print(f"OK -> {draft / name} + review.html （操作{len(op_rows)} 行为{len(be_rows)} 活动{len(ac_rows)}）")


if __name__ == "__main__":
    main()
