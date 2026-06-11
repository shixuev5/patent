# 审查员工作台 · 未来形态（North Star）

这是产品「面向未来模型能力」的最终形态设计稿与讨论记录。当前产品停留在「工具集」形态，本文件夹尝试定义 5–10 年的产品北极星，并以可点击的高保真原型呈现。

- **原型**：[prototype.html](./prototype.html)（默认浅色，右上角切换深色）
- **客户访谈脚本**：[customer-validation.md](./customer-validation.md) — 议题 G
- **MVP 切片设计**：[mvp-slice.md](./mvp-slice.md) — 议题 F
- **状态**：v0.3 · 概念草案，未经客户验证
- **下一步**：见文末「待讨论 / 待验证」

---

## 1. 北极星（一句话）

> **审查员的工作从「操作工具完成审查」转变为「裁决 AI 已经做完的审查」。**

AI 持续在后台对每个案件跑完整流程（检索 → 比对 → 创造性判断 → OA/决定草拟），只在分歧点、低置信度、或法律必需人判的环节，把审查员叫进来。审查员的角色是**裁判 + 策略制定者**，不是工人。

## 2. 关键定位（两条线交集）

| 维度 | 选择 | 业界现状 |
|------|------|----------|
| 目标用户 | 专利局审查员（examiner-facing）| USPTO/EPO/CNIPA 都还停在「嵌入式工具集 + 强制使用」，没有 agent 产品 |
| 自主程度 | Disagreement-first | 业界公开讨论但无落地产品 |

**这是业界两个空白象限的交集**。

---

## 3. 四个核心界面

原型按下列顺序展开。每个界面都对应一个设计决策。

### View 1 · Decision Queue（默认入口）
- **决策**：默认入口不是检索框、不是对话框，而是「今天需要你裁决的事」
- **机制**：5 类排序信号 — 法定时限 > AI 置信度 > 高影响 > 分歧度 > 领域
- **关键 UX**：每条决策点显式暴露 confidence + 历史一致性 + 影响力评级；高置信案件可一键确认（⌘↵）
- **抽样审计**：AI 自主完成的 42 件以独立区域呈现，支持调整抽样率

### View 2 · Reasoning Card（裁决核心）
- **决策**：AI 的产出不是 chat 消息，是结构化「推理书」，与法律文书有同等可审计性
- **三层结构**：
  1. **AI 综合结论** — 由 Orchestrator 整合多个专家 agent
  2. **推理链** — 四步显式展开，每条结论可点击到原文证据
  3. **Agent 间分歧** — 多 agent 投票结果与不同主张并列展示
- **历史一致性**：与审查员过去 12 个月相似案件的判决偏离度，明确暴露
- **裁决动作**：采纳 / 修改 / 深查 / 驳回重跑，四种动作覆盖所有场景

### View 3 · Artifact Canvas（深查模式）
- **决策**：80% 的决策在 Reasoning Card 上一键完成。只有要深入时才进入 canvas
- **三栏布局**：
  - 左：OA 草稿（关键段落带证据高亮）
  - 中：Claim Chart 矩阵（绿/黄/红 三色标注公开程度）
  - 右：Inline Chat — 与 agent 自然对话，agent 操作会直接同步到左/中两栏
- **类比**：Cursor/Claude Code 的 artifact + chat 模式

### View 4 · Policy Console（审查员变成"标准制定者"）
- **决策**：审查员"教"AI 的方式不是写自然语言指令，而是确认/拒绝结构化政策
- **政策来源**：（1）审查员手动编写；（2）Agent 从最近判决中自动提炼候选政策，审查员确认或拒绝
- **关键能力**：
  - **一致性度量** — 显示 AI 跟随政策的比例、异常判决数量
  - **异常判决追踪** — 与政策偏离的判决会被回流分析，可能更新政策也可能修正判决
  - **Backtest** — 改一条政策，先告诉你"过去 90 天的判决会变多少"，降低修改恐惧

---

## 4. 设计原则

| # | 原则 | 体现 |
|---|------|------|
| 1 | AI 主动找人，不是人找 AI | Decision Queue 作为默认入口 |
| 2 | Confidence 永远显式 | 没有 confidence 的 AI 结论不显示 |
| 3 | Agent 工作日志全程可见 | 不是黑盒，审查员能看到 AI 跑了哪几步 |
| 4 | 政策结构化，不是 prompt | Policy Console 用规则 + 案例，不用自然语言指令 |
| 5 | 输出双轨：人可读 + 机器可读 | 为未来 AI-to-AI Negotiation 铺路 |
| 6 | 数据密度高，但视觉克制 | 浅色为主，少用饱和色，accent 集中在关键操作 |

---

## 5. 业界调研结论（2025–2026）

### 产品形态四派
| 派别 | 代表 | 入口 |
|------|------|------|
| Word 侧边栏 copilot | DeepIP（$25M B 轮）| Word 内联 |
| 独立 workspace + Canvas | Rowan、Patlytics（$40M B 轮）、Edge | 浏览器分栏 |
| 单一对话框 Agent | &AI (Andy)、Solve This OA For Me | 对话框为入口 |
| 专利局嵌入式工具集 | USPTO PE2E + SCOUT、EPO ANSERA、CNIPA | 嵌在原审查系统 tab 中 |

### 业界共识 vs 分歧
**收敛中**：
- RAG + ground-truth citation 是底线
- 端到端覆盖（检索→drafting→OA→portfolio）是 attorney 侧产品共识
- Human-in-the-loop 是法定必需，不是产品选择
- 专利局走"工具集 + 强制使用"，三家口径一致

**仍分歧**：
- 入口形态：Word 侧边栏 vs 独立 workspace vs 单一对话框
- Agent 自主程度：copilot vs autonomous（审批模式）
- **Case-first / Inbox 形态空缺** — 真实市场空白
- **AI-to-AI Negotiation 协议** — Goodwin Law 2025.10 公开提出，无产品落地

### 法律红线
- USPTO 37 CFR 11.18 + 2024.4 / 2025.11 guidance：reasonable inquiry + signature + duty of candor
- CNIPA 2026.1.1 生效的修订指南：明确「AI 辅助不替代审查员」
- **结论**：任何产品都不能跨越 human sign-off 红线 → 但「全自动 → human one-click confirm」是可以的

---

## 6. 通向更远的未来：AI-to-AI Negotiation

当本产品在审查员侧建好 agent 之后，天然就是 Goodwin 提出的 "AI-to-AI Negotiation" 的审查员侧端点。

**未来 5–10 年的可能形态**：
- 申请人侧（attorney 用 Patlytics/&AI 这类产品）也部署了 agent
- 双侧 agent 可以通过**结构化协议**直接对接：
  - 申请人 agent 提交 claim 时附带"机器可读论证 + 证据"
  - 审查员 agent 自动评估并回复"机器可读 OA"
  - **只在双方 agent 协商失败时升级到人**
- 谁先做这套协议，谁就定义下一代标准

**MVP 关键决策**：审查员侧 agent 的**输出**需要被设计成「机器可读 + 人可读」双轨。

---

## 7. 讨论记录（v0.1 → v0.2）

### 讨论起点
用户判断："当前产品形态太落后，希望面向未来模型能力重新设计，最终态是完全 AI 的专利审查。"

### 关键收敛点
| 提问 | 我的初判 | 调研后修正 | 最终选择 |
|------|---------|-----------|---------|
| 只一个对话框？ | 不是，应该是 Inbox + Artifact | 业界已有人做对话框（&AI）；Inbox 形态真为空白 | Inbox + Artifact（无人占位） |
| 服务谁？ | 默认审查员 | attorney 侧才是融资热点 | 审查员侧（空白象限）|
| 自主程度 | "Disagreement-first" | 业界公开讨论无落地 | Disagreement-first |

### 已被废弃的方案
- **单一对话框入口** — 与法律文书的结构化产出不匹配，与多案件并行的工作模式不匹配
- **传统 dashboard** — 不能体现 AI 主动性，仍是"人来找 AI"
- **AI 作为辅助 copilot** — 不够激进，与"面向未来模型能力"目标不匹配

---

## 8. 待讨论 / 待验证

| # | 议题 | 状态 |
|---|------|------|
| A | Decision Queue 的具体排序算法（信号权重） | 待细化 |
| B | Reasoning Card 的证据链颗粒度（段落 / 句子 / 短语？）| 待细化 |
| C | Policy Console 的政策表达形式（规则 DSL / 自然语言 + 案例？）| 待细化 |
| D | 与现有审查系统的接入路径（数据/流程/合规）| 待调研 |
| E | AI-to-AI Negotiation 协议草案 | 长期，未启动 |
| F | MVP 边界 — 从北极星到第一版的最短切片 | 待规划 |
| G | 客户验证 — 是否找审查员做访谈？ | 未启动 |

---

## 9. 文件迭代

- **v0.1**（弃）：`/tmp/patent-examiner-future.html`，深色单一主题，Tailwind CDN 加载失败时无样式兜底
- **v0.2**（当前）：[prototype.html](./prototype.html)，浅色为默认 + 深色切换，视觉更克制

迭代时直接修改 `prototype.html`，并在本文件顶部更新版本号 + 在文末追加变更记录。

### 变更记录
- **v0.3 · 2026-05-15** — 补全侧栏的「时限看板 / Ambient Insight / 对话历史」三个视图；新增 ⌘K 命令面板与「抽样审计明细」两个模态；起草 customer-validation.md（议题 G）与 mvp-slice.md（议题 F）两份关键文档。
- **v0.2 · 2026-05-15** — 浅色为默认主题，新增主题切换；视觉系统重构（更少 chip、更克制 accent、统一卡片样式）；卡片增加 box-shadow 与圆角；交互细节优化（hover、citation 样式）。
- **v0.1 · 2026-05-14** — 初版，四个核心界面骨架建立。
