---
name: classroom-annotator
description: 课堂教学视频三级标注流水线（操作→行为→活动）。把一节课堂视频批量转成与金标准同构的三 sheet 标注 xlsx（操作M/行为B/活动A + 证据来源V/R），本地 ASR 字级时间戳 + VLM 看帧 + agent 三级推理 + 评测对齐金标准。触发词：课堂标注、教学行为标注、操作行为活动三级标注、TESTII 标注、课堂视频打标。
---

# 课堂三级标注流水线（classroom-annotator）

把一节课堂视频变成「操作 M / 行为 B / 活动 A」三级标注 xlsx，格式与人工金标准完全同构；定位是 **AI 预标注 + 人工校对**，不是全自动。依据《标注方案.docx》：11 类操作 + 12 类工具 + 加涅九段教学行为 + 三类教学活动（新知学习NK / 评价EL / 总结迁移SF），行为级带活动理论四要素（主体/客体/工具/共同体）。

## 何时用 / 不用

**用**：拿到课堂视频（含音频），要产出三级标注 xlsx 或批量预标注多节课。
**不用**：只要转录/字幕 → `media-transcribe`；口播清理 → `koubo-clean`。

## 目录约定（每节课一个目录，目录名=课程名）

```
<课程名>/
  <课程名>.mp4            # 输入视频
  标注方案.docx            # 标注规则（prompt 素材，可选）
  <课程名>.xlsx           # 金标准（仅评测时需要）
  transcripts/            # Stage1 ASR 四件套 + _progress.json
  frames/frame2s_*.jpg    # Stage2 每2秒一帧
  vlm/grid_w*.jpg         # Stage3 60s窗口×6帧拼图
  vlm/events_w*.json      # Stage3 VLM 视觉事件（代理写入）
  llm/tasks/seg_*.md      # Stage4 任务包
  llm/operations.json     # Stage4a 输出（操作级）
  llm/behaviors.json      # Stage4b 输出（行为级）
  llm/activities.json     # Stage4c 输出（活动级）
  draft/<课程名>_预标注.xlsx + review.html + eval_report.md
```

## 六阶段流程

### Stage 1 — ASR 转录（本地，约 4 分钟/48 分钟课）

```bash
python scripts/stage1_asr.py <课程名>/<课程名>.mp4 --out-dir <课程名>/transcripts
```

关键机制（课堂录音必踩的坑都处理了）：
- **VAD 静音跳过**：学生做题段是静音，直送 LLM-ASR 会幻觉重复直至 llama.cpp `GGML_ASSERT` abort()。先用 ffmpeg silencedetect（-38dB/1.2s）取有声段补集。
- **进程隔离**：少数困难段（远场/噪音）仍会触发 abort()，Python except 接不住。调度器模式循环拉起 worker 子进程；进度不前进就把当前段标记 failed 跳过（前一次机会用 `--chunk-size 8` 小片重试）。
- 断点续跑：`_progress.json` 逐段落盘，重跑只补缺。
- 验收：`_timed.md` 存在非空、合并 json 时间戳项数 > 1000（45 分钟课量级）、0 或极少 failed 段。

### Stage 2 — 抽帧（本地，秒级）

```bash
ffmpeg -y -v error -i <视频> -vf "fps=0.5,scale=960:-2" -q:v 3 "<课程名>/frames/frame2s_%05d.jpg"
```

### Stage 3 — 拼网格 + VLM 看帧（视觉事件检测）

```bash
python scripts/stage3_grid.py --frames <课程名>/frames --out <课程名>/vlm
```

看帧路线二选一（按会话可用性）：

**首选：派 vision 子代理原生看图**（vision 本身是多模态模型，Read 图片直接进上下文，无需 MCP，无 30s 超时）。按 10-12 张/批派后台 vision 代理，指令要点：逐张 `Read` grid 图 → 原生看每帧教师位置/动作/屏幕黑板内容 → 帧间变化推断时间线 → Write 写 `vlm/events_w{XXX}.json`。注意：vision 代理类型注册自 `~/.zcode/agents/vision.md`，其工具清单改动要新会话才生效。

**兜底：主 agent + zai MCP**（`mcp__zai-mcp-server__analyze_image`，本地路径直接传 image_source）。**全局并行 ≤ 2 个调用**（多了 30s 超时；失败重试一般第 2 次过；批量时最多 2 个后台代理）。

两种路线共用 prompt 模板：

> 这是课堂视频网格截图，{学段学科}课{时间范围}的6帧（按时间顺序左上到右下，左上角黄色时间戳）。观察每帧教师位置（讲台前/黑板前/白板旁/学生座位间/画面外）、教师动作、屏幕或黑板显示内容，帧间对比推断动作时间线。输出JSON数组：{"start":"HH:MM:SS","end":"HH:MM:SS","action":"动作","location":"教师位置","screen":"屏幕/黑板内容","evidence":"画面证据描述"}。action从：书写/擦改/展示/指向/播放/使用电脑/演示/巡视/观察/站立讲述/静默/学生展示发言 中选。事件按start升序，相邻事件首尾相接。允许±10秒误差。只输出JSON。

验收：`ls vlm/events_w*.json | wc -l` == 窗口数（grid_w 数量）。

### Stage 4 — 三级标注推理（agent 自己做，这是"封装成 skill"的核心）

```bash
python scripts/stage4_prep.py --course <课程名>     # 生成 llm/tasks/seg_*.md 任务包
```

任务包 = 每 5 分钟一段的「视觉事件时间线 + 逐句转录 + 操作规则速查」。然后 agent：

1. **4a 操作级**：逐段读 `seg_XX.md`，输出该段操作数组（格式见任务包尾部 OP_RULES），合并写 `llm/operations.json`。融合原则：转录句边界为主轴切言语类操作（讲述/提问），视觉事件插入/校正视觉类操作（书写/指向/使用/巡视/观察），两线融合成不重叠时间轴。粒度按金标准校准（中位 15s），不抠 1-10 秒。
2. **4b 行为级**：读全量 operations.json + 转录全文，把连续操作按教学目的聚合成行为段，归入九类之一，填活动理论四要素（客体编码参考：分析归纳类 AN-CK，综合创造类 CR-CK），写 `llm/behaviors.json`。判定依据用标注方案 docx 里的定义和易混辨析（自问自答=讲述不是提问；翻页=使用；让学生看新页面=展示）。
3. **4c 活动级**：行为按学习任务聚合为 NK/EL/SF 三类活动，回填 behavior_id/activity_id 关联，写 `llm/activities.json`。

JSON 契约：operations 每条 `{start, end, op_type, tool, org, org_code, evidence_v, evidence_r, note, behavior_id}`；behaviors `{id:"B0001", activity_id, start, end, behavior_type, subject, subject_code, object_desc, object_code, community, community_code, evidence_v, evidence_r, note}`；activities `{id:"A0001", start, end, activity_type, core_task, subject, subject_code, object_desc, object_code, org, org_code, evidence_note, note}`。start/end 为秒。

### Stage 5 — 导出 xlsx + 校对页

```bash
python scripts/stage5_export.py --course <课程名>
```

产出三 sheet xlsx（列名与金标准一致）+ `review.html`（点开始时间跳视频，标注员只改不写）。

### Stage 6 — 评测（有金标准时）

```bash
python scripts/stage6_eval.py --course <课程名> --gold <金标准.xlsx>
```

指标：覆盖率 / 边界 IoU 均值中位 / 类型一致率 / 数量比。产出 `draft/eval_report.md`。首测基线（澳大利亚课）属内部评测文档，不随仓库发布；首次在自己的数据上跑时，先人工核对一节闭环结果再批量。

## 批量 SOP

多节课：每节一个目录，Stage 1/2/3 串行跑（ASR 占 GPU），Stage 4 推理可穿插做；先跑一节完整闭环 + 评测，指标可接受再批量。新学科只改 VLM prompt 里的 `{学段学科}` 语境词。

## 已知弱点（校对重点）

1. 无说话人分离——师生声音混在转录里，靠内容风格和画面推断，学生汇报段主体归属靠校对。
2. 视觉边界 ±10s（VLM 窗口帧间隔），讲述/提问边界受 ASR 句聚合影响 ±2s。
3. VLM 单帧动作分类偶有漂移（如把"围绕PPT讲述"标成"演示"），Stage 4 融合时按 OP_RULES 规则校正。
