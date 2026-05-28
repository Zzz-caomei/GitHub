# duanjujingtoufantui.skill

一个把参考视频或图片拆解成可复用视频生成关键词的轻量级工作流 / Web 工具。适合短剧、广告、电商 Listing、AI 视频制作团队，把素材里的镜头效果、运镜方式、光线、节奏和负面关键词整理成可复制的生成提示词。


## 核心用途

这个项目提供一套“短剧镜头反推”的参考实现：把用户上传的视频或图片抽成关键帧，再交给使用者自己的视觉大模型进行解读，最终得到镜头效果、运镜方式、光线色彩、节奏拆解、正向关键词、负面关键词和可复制的视频生成关键词。

项目本身不绑定任何私有模型，也不包含 API Key。使用者可以配置 OpenAI 官方接口，或者任何兼容 OpenAI Responses API 的中转接口。只要他们的大模型具备视觉理解能力，就可以基于自己的模型完成素材拆解。

## 功能特点

- 上传参考视频或图片
- 自动抽取关键帧
- 生成关键帧联系表
- 自动切分镜头段落
- 调用 OpenAI 兼容接口分析画面
- 输出镜头效果、运镜、光线、节奏、正向关键词、负面关键词和最终视频生成关键词
- 支持内部工具模式，无需登录即可给团队使用
- 支持账号模式，可按项目隔离素材和报告
- 支持本地运行或 VPS 部署

## 技术栈

- Python 3.11+
- FastAPI
- Jinja2 模板
- OpenCV
- Pillow
- SQLite
- OpenAI Responses API / OpenAI-compatible relay API

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`：

```env
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.5
AUTH_DISABLED=true
```

启动：

```bash
set -a
source .env
set +a
uvicorn app.main:app --host 0.0.0.0 --port 8090
```

打开：

```text
http://127.0.0.1:8090
```

## 环境变量

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `SHOT_PLATFORM_HOME` | 运行数据目录 | `/opt/shot-analysis-platform` |
| `OPENAI_API_KEY` | OpenAI 或中转 API Key | 空 |
| `OPENAI_BASE_URL` | OpenAI 兼容接口地址 | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | 使用的模型 | `gpt-5.5` |
| `AUTH_DISABLED` | 是否关闭登录，作为内部工具直接使用 | `false` |
| `MAX_UPLOAD_MB` | 最大上传文件大小 | `300` |
| `ADMIN_USER` | 管理员账号 | `admin` |
| `ADMIN_PASSWORD` | 管理员密码 | `change-me` |

## 数据与隐私

运行时产生的数据默认在 `SHOT_PLATFORM_HOME` 下：

```text
data/uploads/          上传素材
data/frames/           抽取帧
data/contact_sheets/   关键帧联系表
data/reports/          Markdown 报告
data/database.sqlite   SQLite 数据库
knowledge_base/        知识库沉淀
logs/                  日志
```

这些目录不应该提交到 GitHub。本仓库只包含程序代码和示例配置，不包含任何 API Key、真实素材、客户资料或数据库。

## OpenAI 兼容中转接口

如果你使用的是 OpenAI-compatible relay，把 `.env` 改成类似：

```env
OPENAI_API_KEY=your_relay_key_here
OPENAI_BASE_URL=https://your-relay.example.com/v1
OPENAI_MODEL=gpt-5.5
```

程序会调用：

```text
{OPENAI_BASE_URL}/responses
```

如果接口返回 401，一般是 Key 无效、过期或额度不足。

## 部署建议

- 团队内部使用时可以设置 `AUTH_DISABLED=true`，但建议只开放给可信网络。
- 公网部署时建议放在 nginx / Caddy 后面，并启用 HTTPS。
- 上传素材可能包含敏感内容，请做好访问控制、备份和清理策略。
- 不要把 `.env`、数据库、上传素材、报告和日志提交到 GitHub。

## 许可证

MIT License
