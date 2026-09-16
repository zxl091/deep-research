# DeepResearch 前端

React + TypeScript + Vite，包含研究工作台、知识库、会话与记忆、数据库探索及登录页面。

准备 Node.js 环境和项目依赖后，将 `.env.example` 复制为 `.env`。默认 API 前缀为 `/api`，Vite 代理到 `http://127.0.0.1:8000`。

```powershell
npm run dev
npm run build
```

开发地址：`http://127.0.0.1:5183/`。本机统一启停入口位于项目根目录。

- `src/pages/research/`：研究工作台、报告和摘要管理。
- `src/pages/knowledge/`：文档上传、处理状态与切片查看。
- `src/pages/database/`：业务数据浏览与自然语言查询。
- `tests/`：独立的界面回归与 README 演示入口，使用模拟数据。

更多说明见 [项目 README](../README.md)。
