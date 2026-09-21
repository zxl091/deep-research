# README 展示图

这三张图片是当前前端组件的浏览器截图。为避免展示个人账户、真实会话或未验收报告，页面使用独立的只读示例数据。

| 文件 | 展示内容 |
| --- | --- |
| `workspace.png` | 研究首页、资料范围与模式选择 |
| `research-report.png` | 左侧研究对话、右侧宽幅报告、比较表与成果切换 |
| `session-memory.png` | 自动摘要、关键洞察、关注主题与来源会话 |

截图中的报告和摘要为人工编写的演示内容，完成状态与索引状态也是界面示例，不能用于证明真实模型效果、检索质量或性能。图片未包含账号、密钥或真实 API 响应。

## 复现

1. 使用项目已有前端开发环境启动 Vite。
2. 分别打开 `/tests/readme.html?view=home`、`/tests/readme.html?view=report`、`/tests/readme.html?view=memory`。
3. 记忆页面点击「长期摘要」后截图。

演示入口为 [readme.tsx](../../frontend/tests/readme.tsx)，复用 `BaseLayout`、研究工作台及会话记忆页面。接口响应在该独立入口内拦截，拒绝写入请求；不需要登录，不调用真实后端或模型，也不修改正式应用的路由和鉴权。

截图日期：2026-09-21。
