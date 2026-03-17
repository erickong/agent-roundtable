# Multi-Agent Roundtable Meeting System

[中文版](#中文说明) | [English](#english)

---

## English

### Overview

A multi-agent roundtable discussion system where multiple AI experts debate a given topic through structured rounds, guided by a moderator/arbiter. The system simulates a real expert panel meeting:

1. **User** submits a topic
2. **Moderator** opens the meeting, states rules and objectives
3. **Experts** (3-6 AI agents) independently present initial views (Round 1)
4. Experts challenge each other's points and introduce new ideas (Round 2)
5. Experts defend, revise, and converge (Round 3)
6. Optional Round 4 for unresolved key issues
7. **Moderator** produces a final report with consensus, disagreements, recommendations, and scores

### Features

- **Multi-provider LLM support** — configure multiple OpenAI-compatible providers with weighted selection
- **Separate moderator LLM** — the arbiter can use a different model from the experts
- **Web UI** — WeChat-style group chat interface with real-time WebSocket streaming (port 3088)
- **CLI mode** — run meetings from the command line, suitable for automation and agent-to-agent interaction
- **Web search** — optional Tavily search integration for background research before the meeting
- **Post-meeting chat** — after the meeting ends, continue chatting with the moderator about the results
- **Bilingual UI** — English / Chinese toggle
- **Session persistence** — chat history saved in browser localStorage

### Quick Start

1. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment**

   Unix/macOS:

   ```bash
   cp .env.example .env
   ```

   PowerShell:

   ```powershell
   Copy-Item .env.example .env
   ```

   Then edit `.env` with your API keys.

   Required configuration:
   - `MODERATOR_LLM_*` — at least one moderator LLM (API key, base URL, model)
   - `LLM_PROVIDER_1_*` — at least one expert LLM provider
   - `SEARCH_API_URL` — (optional) local Tavily-compatible news service for recent structured news
   - `TAVILY_API_KEY` — (optional) open-web search for Eastmoney/Xueqiu/Sina/announcements/research
   - `WEB_SEARCH_API_URL` — (optional) custom Tavily-compatible open-web search backend

   If `.env` is missing on first run, the CLI/server will exit with a clear error message.
   Standard local workflow is: copy `.env.example` to `.env`, fill in keys, then restart.

3. **Run web server**

   ```bash
   python web_server.py
   ```

   Or with Uvicorn:

   ```bash
   uvicorn web_server:app --host 0.0.0.0 --port 3088
   ```

   Open <http://localhost:3088> in your browser.

4. **Run via CLI**

   ```bash
   python main.py "How to design a multi-agent stock research system?"
   python main.py "Topic" --goal "Find 3 actionable solutions" --background "context info"
   python main.py "Topic" --search  # Enable web search before meeting
   python main.py "Topic" --constraint "Budget under $10k" --constraint "Must use Python"
   ```

### Configuration (.env)

| Variable | Description |
| --- | --- |
| `MODERATOR_LLM_NAME` | Moderator provider name |
| `MODERATOR_LLM_BASE_URL` | OpenAI-compatible API base URL |
| `MODERATOR_LLM_API_KEY` | API key |
| `MODERATOR_LLM_MODEL` | Model name |
| `MODERATOR_LLM_TIMEOUT` | Request timeout in seconds (default: 300) |
| `LLM_PROVIDER_N_NAME` | Expert provider N name |
| `LLM_PROVIDER_N_BASE_URL` | Expert provider N base URL |
| `LLM_PROVIDER_N_API_KEY` | Expert provider N API key |
| `LLM_PROVIDER_N_MODEL` | Expert provider N model |
| `LLM_PROVIDER_N_WEIGHT` | Selection weight (higher = more likely, default: 1) |
| `LLM_PROVIDER_N_TIMEOUT` | Request timeout in seconds (default: 300) |
| `SEARCH_API_URL` | Tavily-compatible local news API URL |
| `TAVILY_API_KEY` | Tavily API key for open-web search |
| `WEB_SEARCH_API_URL` | Optional Tavily-compatible open-web search API URL |

### Project Structure

```text
├── main.py                 # CLI entry point
├── web_server.py           # FastAPI web server (port 3088)
├── config.py               # .env configuration loader
├── models.py               # Data structures
├── agents.py               # Expert and Moderator agent classes
├── meeting.py              # Meeting orchestrator (CLI)
├── meeting_streaming.py    # Streaming meeting orchestrator (WebSocket)
├── prompts.py              # Prompt templates
├── parser.py               # JSON/Markdown response parser
├── search.py               # Tavily search integration
├── static/index.html       # Web UI
├── tests/                  # Minimal release smoke tests
├── .env.example            # Example configuration
├── requirements.txt        # Python dependencies
└── SPECDOC.md              # Full specification document
```

---

## 中文说明

### 概述

一个多Agent圆桌讨论系统，多个AI专家围绕给定议题进行结构化多轮辩论，由仲裁者引导。系统模拟真实的专家圆桌会议：

1. **用户** 提出议题
2. **仲裁者** 开场，声明规则和目标
3. **专家** (3-6个AI Agent) 独立陈述初始观点（第一轮）
4. 专家互相质疑、引入新观点（第二轮）
5. 专家辩护、修正、收敛（第三轮）
6. 可选第四轮补充关键未解问题
7. **仲裁者** 产出最终报告，包含共识、分歧、推荐方案和评分

### 功能特性

- **多LLM提供商** — 支持多个OpenAI兼容的LLM服务商，按权重随机选择
- **独立仲裁者LLM** — 仲裁者可以使用与专家不同的模型
- **Web界面** — 微信风格群聊界面，WebSocket实时推送（端口3088）
- **命令行模式** — 通过CLI运行会议，适合自动化和Agent间交互
- **联网搜索** — 可选的Tavily搜索，会议前自动获取背景信息
- **会后对话** — 会议结束后可继续与仲裁者对话讨论结果
- **中英双语** — 界面支持中英文切换
- **会话持久化** — 聊天记录保存在浏览器localStorage

### 快速开始

1. **安装依赖**

   ```bash
   pip install -r requirements.txt
   ```

2. **配置环境变量**

   macOS / Linux:

   ```bash
   cp .env.example .env
   ```

   PowerShell:

   ```powershell
   Copy-Item .env.example .env
   ```

   然后编辑 `.env` 填入你的 API 密钥。

   必须配置：
   - `MODERATOR_LLM_*` — 至少一个仲裁者LLM（API密钥、地址、模型）
   - `LLM_PROVIDER_1_*` — 至少一个专家LLM提供商
   - `TAVILY_API_KEY` — （可选）用于联网搜索功能
   - `SEARCH_API_URL` — （可选）使用本地 Tavily 兼容搜索服务替代 Tavily

   如果首次启动时缺少 `.env`，CLI/服务端会直接退出并提示错误。
   标准本地流程就是：复制 `.env.example` 为 `.env`，填写密钥后重新启动。

3. **启动Web服务**

   ```bash
   python web_server.py
   ```

   或使用 Uvicorn：

   ```bash
   uvicorn web_server:app --host 0.0.0.0 --port 3088
   ```

   浏览器打开 <http://localhost:3088>

4. **命令行运行**

   ```bash
   python main.py "如何设计一个多agent协作做股票研究的系统？"
   python main.py "议题" --goal "找到3个可执行方案" --background "背景信息"
   python main.py "议题" --search  # 启用联网搜索
   python main.py "议题" --constraint "预算10万以内" --constraint "必须使用Python"
   ```

### 配置说明 (.env)

| 变量名 | 说明 |
| --- | --- |
| `MODERATOR_LLM_NAME` | 仲裁者提供商名称 |
| `MODERATOR_LLM_BASE_URL` | OpenAI兼容API地址 |
| `MODERATOR_LLM_API_KEY` | API密钥 |
| `MODERATOR_LLM_MODEL` | 模型名称 |
| `MODERATOR_LLM_TIMEOUT` | 请求超时（秒，默认300） |
| `LLM_PROVIDER_N_NAME` | 专家提供商N名称 |
| `LLM_PROVIDER_N_BASE_URL` | 专家提供商N地址 |
| `LLM_PROVIDER_N_API_KEY` | 专家提供商N API密钥 |
| `LLM_PROVIDER_N_MODEL` | 专家提供商N模型 |
| `LLM_PROVIDER_N_WEIGHT` | 选择权重（越大越可能被选中，默认1） |
| `LLM_PROVIDER_N_TIMEOUT` | 请求超时（秒，默认300） |
| `SEARCH_API_URL` | Tavily兼容的搜索API地址（如本地新闻服务） |
| `TAVILY_API_KEY` | Tavily搜索API密钥（免费注册: <https://tavily.com>） |

### 项目结构

```text
├── main.py                 # 命令行入口
├── web_server.py           # FastAPI Web服务（端口3088）
├── config.py               # .env 配置加载器
├── models.py               # 数据结构
├── agents.py               # 专家和仲裁者Agent类
├── meeting.py              # 会议编排器（CLI用）
├── meeting_streaming.py    # 流式会议编排器（WebSocket用）
├── prompts.py              # Prompt模板
├── parser.py               # JSON/Markdown响应解析器
├── search.py               # Tavily搜索集成
├── static/index.html       # Web界面
├── tests/                  # 最小发布冒烟测试
├── .env.example            # 配置示例
├── requirements.txt        # Python依赖
└── SPECDOC.md              # 完整规格文档
```
