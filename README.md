# 高处作业安全审查与预警智能体

面向建筑施工安全全过程管理的项目级智能辅助平台。系统以施工方案审查、班前风险分析和现场多模态巡检作为多源风险发现入口，将方案控制要求贯穿到实际作业执行，并通过人工确认、整改派单、证据核验、复核销项和安全日志实现全过程留痕。

## 主要功能

- **施工方案审查**：解析 DOC/DOCX 方案，结合结构化规则与规范 RAG 识别问题；支持上传修订版并对比原问题的整改情况。
- **班前风险分析**：通过文字或语音描述当日作业，结合施工方案、规范、事故案例、天气和现场环境生成班前风险提示卡。
- **现场隐患巡检**：基于现场图片和文字描述生成“疑似隐患候选”；AI 不直接形成正式隐患，必须由安全管理人员确认。
- **隐患整改记录**：完成安全事项确认、整改工单、整改证据提交、人工复核、关闭或退回，并保存完整事件时间线。
- **安全日志生成**：汇总方案审查、班前风险、现场巡检及整改闭环数据，生成包含整改前后图片的 Word 安全日志。
- **智能助手**：支持文字和语音问答，可调用项目知识、风险清单和通用联网搜索能力。
- **账号与项目管理**：支持注册、登录以及一个账号管理和切换多个项目，项目数据相互隔离。

## 技术栈

- 前端：Vue 3、TypeScript、Vite、Pinia、ECharts
- 后端：FastAPI、Pydantic、SQLite
- Agent：LangGraph、LangChain Core、MCP
- 模型：阿里云百炼 OpenAI 兼容接口（现场识别默认配置为 Qwen3.7-Plus）
- 知识检索：Chroma、结构化规则库、施工规范、事故知识图谱
- 部署：Docker Compose、Nginx

## 项目结构

```text
backend/          FastAPI 接口、Agent 编排、规则计算、RAG 与业务服务
frontend-vue/     Vue 3 主前端
frontend/         后端兼容页面与静态调试资源
data/             运行和测试所需的最小规则、规范、知识图谱及样例数据
deploy/           Nginx 生产配置
tests/            后端自动化测试
```

本地运行产生的数据库、上传图片、日志、Word 报告、密钥文件和虚拟环境均不纳入仓库。

## 本地开发

后端：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

前端构建：

```powershell
Set-Location frontend-vue
npm install
npm run build
Set-Location ..
```

启动：

```powershell
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

访问 <http://127.0.0.1:8000>。开发环境默认演示账号为 `Admin`，密码为 `admin`；部署到公开环境前应修改账号策略和默认密码。

## Docker 启动

建议使用 Docker Compose：

```bash
git clone https://github.com/yangguotyao/height-work-safety-agent.git
cd height-work-safety-agent
cp .env.example .env
docker compose -f docker-compose.prod.yml up -d --build
```

## 环境变量

真实密钥只填写在本机或服务器的 `.env` 中，不要提交到 Git 仓库。

| 配置 | 用途 |
| --- | --- |
| `MODEL_PROVIDER` | 通用模型提供方；本地可使用 `mock` |
| `MODEL_API_KEY` / `MODEL_BASE_URL` / `MODEL_NAME` | 方案审查及通用模型配置 |
| `HAZARD_VISION_API_KEY` / `HAZARD_VISION_BASE_URL` | 现场图片识别与整改对比模型配置 |
| `BOCHA_API_KEY` | 智能小助手通用联网搜索 |
| `PROJECT_LONGITUDE` / `PROJECT_LATITUDE` | 项目天气位置 |
| `CAIYUN_WEATHER_TOKEN` | 彩云天气凭证 |

完整配置项见 `.env.example`。

## 验证

```bash
python -m pytest -q
cd frontend-vue
npm run build
```

## 使用边界

本项目用于安全辅助识别、风险提示和过程留痕，不替代法定施工方案审查、作业许可、现场检查、专业人员签字或安全管理人员的最终判断。
