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

## v1.3.0 - 2026-04-12 - 邮件通知、微信接入
- feature: 提供邮件通知功能，用于接收任务消息
- feature: 提供微信接入，现在可以通过微信来创建 AI 任务

## v1.2.0 - 2026-04-05 - AI 答复上线
- feature: AI 答复功能正式推出
- feature: 提供 AI 检索功能（Beta）

## v1.1.0 - 2026-03-07 - 个人空间上新
- feature: 增加用户登录认证
- feature: 增加个人空间页面

## v1.0.0 - 2026-03-05 - 网站可视化重构
- feature: 重构网站首页，提供更好的交互体验
- feature: 提供 AI 答复功能 (Beta)

## v0.1.0 - 2025-02-13 - 网站上线，提供 AI 分析
- feature: 提供 AI 分析功能
