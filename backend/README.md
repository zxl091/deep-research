# DeepResearch 后端

应用入口：`app/app_main.py`。统一依赖声明：`requirements.txt`。

当前发布接口包括登录认证、会话管理、知识库、数据库探索和 `/assistant` 研究运行时。六角色编排位于 `app/service/assistant/full_research.py`，阶段持久化、取消和恢复位于 `runtime.py`。

本机启停使用项目根目录的 PowerShell 脚本。首次配置需准备 Python 环境及依赖，将 `.env.example` 复制为 `.env` 并填写服务参数。

在已配置环境中，从本目录运行后端：

```powershell
python app/app_main.py
```

数据库依赖统一由根目录 `docker-compose.yml` 描述；执行分析代码使用 `sandbox/` 中的独立 Docker 镜像。完整架构与测试入口见 [项目 README](../README.md)。
