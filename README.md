# 高处作业安全审查与预警智能体

面向建筑施工高处作业的项目级安全辅助平台，提供施工方案审查、安全培训、动态风险分析、安全日志生成和智能问答能力。

- 在线演示：<http://8.133.233.43/>
- 后端接口文档：<http://8.133.233.43/docs>

## 主要功能

- **方案审查**：上传 DOCX 或 DOC 施工方案，结合结构化规则和规范 RAG 输出可追溯问题与整改建议。
- **安全培训**：通过对话采集每日作业任务，生成风险提示卡，并提供场景测验、错题和学习记录。
- **风险分析**：综合任务、天气、方案问题和学习记录，生成可版本化的动态风险结果。
- **日志生成**：汇总当日项目数据生成 Word 安全日志，并通过智能小助手查询项目资料或通用安全知识。
- **项目工作空间**：不同项目独立保存业务数据、上传文件和向量索引，支持新建、切换、改名和删除。

## 技术栈

- 前端：Vue 3、TypeScript、Vite、Pinia、ECharts
- 后端：FastAPI、Pydantic、SQLite
- Agent：LangGraph、LangChain Core、MCP
- 知识检索：Chroma、结构化规则库、事故知识图谱
- 部署：Docker Compose、Nginx

## 项目结构

```text
backend/          FastAPI、Agent、规则计算和数据服务
frontend-vue/     Vue 3 主前端
frontend/         兼容页面与调试资源
data/             运行和测试所需的最小规则、规范及样例数据
deploy/           Nginx 生产配置
tests/            后端自动化测试
```

## Docker 启动

建议使用 Docker Compose：

```bash
git clone https://github.com/yangguotyao/height-work-safety-agent.git
cd height-work-safety-agent
cp .env.example .env
docker compose -f docker-compose.prod.yml up -d --build
```

启动后访问：

- Web：<http://127.0.0.1/>
- API 文档：<http://127.0.0.1/docs>
- 健康检查：<http://127.0.0.1/health/ready>

默认使用 `MODEL_PROVIDER=mock`，无需外部模型密钥即可启动并体验确定性业务流程。

## 本地开发

后端：

```powershell
conda activate height-work-agent
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m uvicorn backend.app.main:app --reload
```

前端：

```bash
cd frontend-vue
npm ci
npm run dev
```

## 环境变量

真实密钥只填写在本机或服务器的 `.env` 中，不要提交到 Git 仓库。

| 配置 | 用途 |
| --- | --- |
| `MODEL_PROVIDER` | `mock` 或 OpenAI 兼容模型 |
| `MODEL_API_KEY` | 模型 API 密钥 |
| `MODEL_BASE_URL` | OpenAI 兼容接口地址 |
| `MODEL_NAME` | 模型名称 |
| `BOCHA_API_KEY` | 智能小助手通用联网搜索 |
| `PROJECT_LONGITUDE` / `PROJECT_LATITUDE` | 项目天气位置 |
| `CAIYUN_WEATHER_TOKEN` | 彩云天气凭证 |

完整配置项见 `.env.example`。

## 测试

```bash
python -m pytest -q
cd frontend-vue
npm run build
```

## 使用说明

本项目用于安全辅助、风险提示和过程留痕，不替代法定方案审查、作业许可、现场检查或专业人员签字。
