# Changelog

本文件由后端接口 `GET /api/changelog` 解析并返回给前端页面。

## 维护格式（markdown-v1）

每个版本块使用如下格式：

```md
## v<version> - YYYY-MM-DD - <可选标题>
- feature: <新增内容>
- improvement: <优化内容>
- fix: <修复内容>
- breaking: <不兼容变更内容>
```

兼容写法：
- 类型支持中英文别名：`feature/新增`、`improvement/优化`、`fix/修复`、`breaking/变更`
- 条目也支持方括号形式：`- [feature] xxx`
- 条目未标注类型时默认按 `improvement`

## v1.0.0 - 2026-03-05 - 产品化首页与任务路由拆分
- feature: 新增产品化首页，完整展示平台能力、流程与适用场景。
- feature: 新增 /tasks 独立任务页，支持专利分析与审查意见答复任务。
- feature: 新增 /changelog 页面并接入后端 changelog 接口。
- improvement: 首页升级为渐变玻璃科技风，提升品牌感与可读性。

## v0.5.0 - 2026-03-04 - 审查意见答复能力上线
- feature: 任务系统支持 office_action_reply 类型。
- feature: 新增多文件上传能力，支持 PDF 与 DOCX 材料。
- improvement: 任务卡片展示任务类型与报告下载状态。

## v0.4.0 - 2026-03-02 - 任务系统基础能力完善
- feature: 支持任务创建、进度跟踪与结果下载。
- fix: 修复任务状态同步偶发不一致问题。
