# classroom-annotator

课堂教学视频三级标注 Agent Skill：把一节课堂视频批量转成与人工金标准同构的三 sheet 标注 xlsx（操作 M / 行为 B / 活动 A，证据来源 V/R），定位是 **AI 预标注 + 人工校对**，不是全自动。

标注体系依据《标注方案》：11 类操作 + 12 类工具 + 加涅九段教学行为 + 三类教学活动（新知学习 NK / 评价 EL / 总结迁移 SF），行为级带活动理论四要素（主体 / 客体 / 工具 / 共同体）。

## 适用范围

**用**：拿到课堂视频（含音频），要产出三级标注 xlsx，或批量预标注多节课。

**不用**：只要转录/字幕 → 用 `media-transcribe`；口播清理 → 用 `koubo-clean`。

## 工作原理

六阶段流水线，每节课一个工作目录：

```text
Stage 1  本地 ASR 转录（字级时间戳，VAD 静音跳过 + 进程隔离防崩溃）
Stage 2  ffmpeg 抽帧（每 2 秒一帧）
Stage 3  拼 60s 窗口网格图，VLM 看帧产出视觉事件时间线
Stage 4  Agent 三级推理：操作级 → 行为级 → 活动级（skill 核心）
Stage 5  导出与金标准同构的三 sheet xlsx + 校对 HTML（点时间跳视频）
Stage 6  有金标准时自动评测：覆盖率 / 边界 IoU / 类型一致率 / 数量比
```

详细指令、JSON 契约与验收标准见 [SKILL.md](SKILL.md)。

## 目录结构

```text
classroom-annotator/
├── SKILL.md                      # Skill 入口（Agent 读取的完整操作指令）
├── agents/openai.yaml            # Codex 等客户端的界面元数据
├── reference/
│   ├── annotation_rules.md       # 判定规则权威来源（金标准校准 R1-R4/RB1-RB2）
│   ├── fewshot_前10分钟.json      # 金标准同构的标注示例（匿名公开课，已脱敏）
│   └── 首测报告_澳大利亚课.md      # v1/v2/v3 三方评测基线与迭代教训
└── scripts/
    ├── stage1_asr.py             # 本地 ASR 转录（调度器 + worker 进程隔离）
    ├── stage3_grid.py            # 抽帧拼 60s 窗口网格图
    ├── stage4_prep.py            # 生成三级推理任务包
    ├── stage5_export.py          # 导出三 sheet xlsx + 校对 HTML
    └── stage6_eval.py            # 与金标准对齐评测
```

## 安装

把仓库克隆到 Agent 的 skills 目录：

```bash
git clone https://github.com/techdou/classroom-annotator.git ~/.agents/skills/classroom-annotator
```

入口为 `SKILL.md`，兼容 ZCode、Claude Code、OpenAI Codex、OpenCode 等支持 Agent Skills 的客户端。

## 依赖

运行环境：

- **ffmpeg / ffprobe**：抽帧与静音检测
- **Python 3.10+**，包：`pandas`、`openpyxl`（写 xlsx）、`Pillow`（拼网格图）
- **[media-transcribe](https://github.com/techdou/media-transcribe) skill**：Stage 1 复用其 `oral_pipeline` 模块
- **qwen3-asr-gguf 引擎**：本地 ASR 推理（GGUF 模型 + llama.cpp），需自行下载部署

外部路径通过环境变量配置（默认值见脚本头部注释）：

| 环境变量 | 含义 | 默认解析位置 |
|---|---|---|
| `MEDIA_TRANSCRIBE_SCRIPTS` | media-transcribe 的 scripts 目录 | `~/.agents/skills/media-transcribe/scripts` |
| `QWEN_ASR_ENGINE_DIR` | qwen_asr_gguf 引擎目录 | `~/models/qwen3-asr-gguf/engine` |
| `QWEN_ASR_MODEL_DIR` | 模型权重目录 | `~/models/qwen3-asr-gguf/model` |

## 快速上手

```bash
# 每节课一个目录，目录名=课程名，放入 <课程名>.mp4
python scripts/stage1_asr.py <课程名>/<课程名>.mp4 --out-dir <课程名>/transcripts
ffmpeg -y -v error -i <课程名>/<课程名>.mp4 -vf "fps=0.5,scale=960:-2" -q:v 3 "<课程名>/frames/frame2s_%05d.jpg"
python scripts/stage3_grid.py --frames <课程名>/frames --out <课程名>/vlm
python scripts/stage4_prep.py --course <课程名>
# Stage 4 三级推理由 Agent 按 SKILL.md 指令完成，随后：
python scripts/stage5_export.py --course <课程名>
python scripts/stage6_eval.py --course <课程名> --gold <金标准.xlsx>   # 有金标准时
```

多节课批量：Stage 1/2/3 串行跑（ASR 占 GPU），Stage 4 推理可穿插；先跑通一节完整闭环 + 评测，指标可接受再批量。

## 数据与隐私

- 课堂视频与全部中间产物（转录、抽帧、xlsx）都在**本地**处理，本 Skill 不上传任何课堂数据。
- 仓库通过 `.gitignore` 排除了全部运行产出物（视频/音频/xlsx/`frames/`/`transcripts/`/`vlm/`/`llm/`/`draft/`）与内部文档，请勿把真实课堂数据提交进仓库。
- `reference/` 下的标注示例来自匿名化公开课，仅作 few-shot 格式参考。

## 已知限制

1. 无说话人分离——师生声音混在转录里，学生汇报段的主体归属靠人工校对。
2. 视觉事件边界 ±10s（VLM 窗口帧间隔），讲述/提问边界受 ASR 句聚合影响 ±2s。
3. VLM 单帧动作分类偶有漂移，Stage 4 融合时按规则校正，仍建议人工通读校对页。

## 许可证

本项目采用 [MIT License](LICENSE)。课堂视频、金标准 xlsx 等用户数据的所有权与使用权归用户所有。
