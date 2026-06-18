# AGENTS.md

面向 Codex / 代码 Agent 的开发说明。

## 必读顺序

1. `CODEX_PROJECT_GUIDE.md`：完整项目说明、架构、模块职责、待办任务。
2. `README.md`：用户运行说明。
3. `.env.example`：配置项说明。
4. `app/pipeline.py`：主流程入口。
5. `app/models.py`：数据结构约束。

## 开发原则

- 每次只改一个模块，避免无必要的大重构。
- 保持 `APP_MOCK_MODE=true` 时始终可以本地跑通。
- 接真实阿里云 API 时，必须保存 raw response，便于排查。
- 新增配置必须同步更新 `.env.example` 和文档。
- 不要提交 API Key、真实电影文件、声音样本或用户隐私数据。
- 声音复刻必须要求用户授权确认，不能克隆未经授权的第三方声音。

## 推荐优先级

1. 跑通 mock 模式和测试。
2. 接入真实 Qwen 文本模型。
3. 接入真实 Qwen 多模态模型。
4. 接入真实 Qwen-TTS。
5. 接入真实 ASR。
6. 补齐声音复刻。
7. 实现局部重生成。
8. 升级审核页。
