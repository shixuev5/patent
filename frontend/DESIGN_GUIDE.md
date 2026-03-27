# Frontend 设计指南（AI 迭代参考）

本指南用于约束后续 AI 对前端页面的设计与实现，目标是保证视觉一致性、交互一致性和可维护性。

## 1. 设计目标

- 风格关键词：专业、清晰、轻量、可信。
- 页面语气：审查员工作台，不做娱乐化视觉。
- 优先级：信息层级清晰 > 交互可预测 > 视觉装饰。
- 改动原则：在现有视觉语言内迭代，避免引入突兀新风格。

## 2. 全局视觉语言

### 2.1 颜色体系

- 主基调：`slate + cyan`。
- 成功/完成：`emerald`。
- 风险/错误：`rose`。
- 警示：`amber`。
- 页面背景：浅色渐变（见 `layouts/default.vue` 的 `app-shell`）。

### 2.2 形状与阴影

- 主卡片圆角：`rounded-3xl`。
- 次级卡片/组件圆角：`rounded-2xl`。
- 小型按钮/标签圆角：`rounded-xl` 或 `rounded-full`。
- 阴影使用轻量 `shadow-sm` 或微弱自定义阴影，避免重阴影。

### 2.3 字体与层级

- 常规文本：`Noto Sans SC`（全局）。
- 品牌/标题点缀：`Orbitron`（仅用于品牌或少量展示性标题）。
- 常规信息字号：`text-sm`。
- 辅助说明：`text-xs` + 低对比度色。
- 标题：`text-xl` / `text-2xl` / `text-3xl`，配合 `font-semibold`。

## 3. 布局规范

### 3.1 页面骨架

- 使用默认布局容器：`max-w-6xl` + 响应式内边距。
- 页面区块间距：以 `space-y-4` 为主，不随意放大。
- 每个功能区块使用卡片容器（边框 + 浅背景 + 圆角）。

### 3.2 响应式

- 主要断点：`sm`（移动端到桌面过渡）。
- 移动端规则：
- 减少横向拥挤，优先单列布局。
- 按钮和点击区域高度不低于 `h-8`。
- 文案不省略关键信息，必要时隐藏次要装饰文本。

## 4. 组件设计约束

### 4.1 表单与上传区

- 输入框统一样式：`rounded-2xl border-slate-200 focus:border-cyan-500 focus:ring-cyan-100`。
- 文件上传框统一 `accept`、边框、文件按钮样式。
- 必填项使用红色星号（`text-rose-500`），不堆叠重复提示文案。
- 校验策略：
- 按钮仅在满足必填条件时可点击。
- 提交函数保留兜底校验（全局 notice）。

### 4.2 按钮

- 主按钮：`bg-cyan-700`，hover 到 `cyan-800`。
- 危险操作：`rose` 色系。
- 禁用态：`disabled:bg-slate-300` + `disabled:cursor-not-allowed`。
- 同一容器内按钮高度、圆角、内边距必须一致。

### 4.3 任务列表（TaskPanel / TaskCard）

- 面板：底部固定，视觉与页面主卡片一致（`slate/cyan`）。
- 卡片状态色：
- `processing` -> `cyan`
- `completed` -> `emerald`
- `error` -> `rose`
- 图标容器、操作按钮、文本行距要统一尺寸节奏。
- 空状态文案保持任务语义，不写与当前模式冲突的提示。

### 4.4 反馈组件

- 全局提示（toast）：简短、可操作、避免技术术语。
- 弹窗（modal）：用于高风险行为确认（如清空任务）。
- 警示信息统一 `amber`，错误信息统一 `rose`。

## 5. 文案与语义规范

- 文案风格：直接、简洁、业务导向。
- 命名一致：
- `AI 分析`、`AI 答复` 保持固定，不引入同义词混用。
- 上传说明写法统一：`（.pdf/.doc/.docx）`。
- 对比文件说明必须包含：仅上传非专利文件（标准、论文、技术手册等）。

## 6. 代码实现规范

- 技术栈：Nuxt 3 + Vue 3 + TypeScript + Tailwind。
- 样式优先使用 Tailwind 原子类，复杂复用样式写在组件 `scoped style`。
- 保持现有状态管理方式（Pinia），避免重复状态源。
- 不要在无需求时新增第三方 UI 库。
- 修改页面时优先复用现有组件结构，不做大规模重写。

## 7. 无障碍与可用性

- 所有可点击元素需有明显 hover/disabled 状态。
- 颜色表达状态时，配合文字或图标，不仅靠颜色区分。
- 表单错误通过清晰文本反馈，不使用含糊提示。
- 文件上传限制要在 UI 文案和逻辑校验中保持一致。

## 8. AI 迭代交付清单（每次改动前后自检）

- 是否仍保持 `slate/cyan` 主风格？
- 新增组件圆角、间距、字号是否与现有组件一致？
- 必填逻辑是否“按钮可点击条件”与“提交校验”双保险？
- 上传格式文案、`accept`、运行时校验是否一致？
- 移动端是否可用（不溢出、按钮可点、文本可读）？
- 是否通过 `npm --prefix frontend run build`？

## 9. 参考文件

- `frontend/layouts/default.vue`
- `frontend/pages/index.vue`
- `frontend/pages/tasks.vue`
- `frontend/components/task/TaskPanel.vue`
- `frontend/components/task/TaskCard.vue`
- `frontend/assets/css/main.css`
