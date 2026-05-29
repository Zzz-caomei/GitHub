# duanjujingtoufantui.skill

短剧镜头反推工具：上传参考视频或图片，抽取关键帧，并调用你自己的视觉大模型生成镜头关键词、运镜脚本和视频生成提示词。

> A lightweight video prompt workbench for turning reference videos or images into reusable AI video generation prompts.

## What It Does

`duanjujingtoufantui.skill` 是一个轻量级 Web 工作台，面向短剧、广告、电商 Listing、AI 视频制作团队。它把参考素材里的镜头效果、运镜方式、光线色彩、节奏氛围和负面关键词整理成可复制的生成提示词，方便复用到新人物、新产品或新场景的视频制作中。

项目本身不内置私有模型，也不包含 API Key。你可以配置 OpenAI 官方接口，或配置兼容 OpenAI Responses API 的中转接口。只要模型具备视觉理解能力，就可以用于素材拆解和关键词生成。

## Use Cases

- 反推爆款短剧镜头的景别、机位、焦段感和节奏。
- 把广告、电商视频或参考图拆成可复用的视频生成关键词。
- 为 AI 视频团队沉淀内部镜头语言和提示词知识库。
- 用同一套参考镜头风格改写到新人物、新产品或新场景。
- 给客户项目按归档、项目和标签管理素材分析报告。

## Features

- 上传常见视频或图片格式。
- 自动抽取视频关键帧，生成关键帧联系表。
- 基于画面变化自动切分镜头段落。
- 调用 OpenAI Responses API 或兼容接口分析视觉内容。
- 输出镜头效果、运镜方式、光线色彩、节奏氛围、正向关键词、负面关键词和最终视频生成提示词。
- 支持内部工具模式：关闭登录后直接给可信团队使用。
- 支持账号模式：管理员可创建用户，并按项目隔离素材和报告。
- 自动沉淀 Markdown 报告和知识库文件，方便二次整理。
- 支持本地运行、VPS 部署、nginx 反向代理和 systemd 托管。

## Tech Stack

- Python 3.11+
- FastAPI
- Jinja2
- SQLite
- OpenCV
- Pillow
- NumPy
- OpenAI Responses API / OpenAI-compatible relay API

## Quick Start

Clone the repository:

```bash
git clone https://github.com/Zzz-caomei/GitHub.git
cd GitHub
```

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```env
SHOT_PLATFORM_HOME=./runtime
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.5
AUTH_DISABLED=true
MAX_UPLOAD_MB=300
ADMIN_USER=admin
ADMIN_PASSWORD=change-me
```

Start the app:

```bash
set -a
source .env
set +a
uvicorn app.main:app --host 0.0.0.0 --port 8090
```

Open:

```text
http://127.0.0.1:8090
```

## Basic Workflow

1. Open the workbench in your browser.
2. Upload a reference video or image.
3. Choose the output type: all dimensions, shot style, camera movement, or 8-second script.
4. Add your analysis goal, such as "extract low-angle push-in, motion blur, cold/warm lighting, and negative prompts".
5. Wait for frame extraction and visual analysis.
6. Copy the generated video prompt or export the full report.

## Environment Variables

| Variable | Description | Default |
| --- | --- | --- |
| `SHOT_PLATFORM_HOME` | Runtime data directory. Keep generated files outside the repository in production. | `/opt/shot-analysis-platform` |
| `OPENAI_API_KEY` | OpenAI or compatible relay API key. | empty |
| `OPENAI_BASE_URL` | OpenAI-compatible API base URL. | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | Vision-capable model used for analysis. | `gpt-5.5` |
| `AUTH_DISABLED` | Set `true` for internal no-login mode. Set `false` to enable login. | `false` |
| `MAX_UPLOAD_MB` | Maximum upload size in MB. | `300` |
| `ADMIN_USER` | Admin username used when authentication is enabled. | `admin` |
| `ADMIN_PASSWORD` | Admin password used when authentication is enabled. | `change-me` |

## Runtime Data

The app writes generated files under `SHOT_PLATFORM_HOME`:

```text
data/uploads/          Uploaded source media
data/frames/           Extracted frames
data/contact_sheets/   Contact sheet previews
data/reports/          Markdown analysis reports
data/exports/          Exported zip files
data/database.sqlite   SQLite database
knowledge_base/        Reusable knowledge-base reports
logs/                  Runtime logs
```

Do not commit runtime data, uploaded media, databases, logs, reports, `.env` files, API keys, or customer materials to GitHub.

## API Compatibility

The application calls:

```text
{OPENAI_BASE_URL}/responses
```

For an OpenAI-compatible relay, configure `.env` like this:

```env
OPENAI_API_KEY=your_relay_key_here
OPENAI_BASE_URL=https://your-relay.example.com/v1
OPENAI_MODEL=your_vision_model
```

If the app returns an authentication error, check whether the API key is valid, the relay supports `/responses`, the account has enough quota, and the configured model supports image input.

## Deployment

For production or team usage:

- Put the app behind nginx or Caddy.
- Enable HTTPS on public networks.
- Set `AUTH_DISABLED=false` when the app is exposed outside a trusted network.
- Set a strong `ADMIN_PASSWORD`.
- Store runtime data outside the repository.
- Configure backups and cleanup rules for uploaded media and reports.

See [docs/deployment.md](docs/deployment.md) for a systemd and nginx example.

## Security And Privacy

Uploaded videos and images may contain sensitive people, products, customer data, or unpublished creative assets. Treat `SHOT_PLATFORM_HOME` as private application data.

- Never publish `.env`, uploaded files, generated reports, SQLite databases, logs, or customer examples.
- Use access control for public deployments.
- Review your model provider or relay provider's data policy before uploading confidential media.
- Report security concerns privately using the process in [SECURITY.md](SECURITY.md).

## Limitations

- Automatic shot segmentation is based on visual change detection and should be reviewed by a human.
- Prompt quality depends on the configured vision model.
- Large videos can take longer to upload, sample, and analyze.
- The default SQLite setup is suitable for lightweight team usage, not high-volume multi-tenant SaaS workloads.

## Project Structure

```text
app/
  main.py              FastAPI application and processing pipeline
  static/style.css     Web UI styles
  templates/           Jinja2 pages
docs/
  deployment.md        Deployment notes
.env.example           Example environment configuration
requirements.txt       Python dependencies
LICENSE                MIT license
```

## Roadmap

- Add screenshot examples for the workbench and report pages.
- Add Docker deployment files.
- Add model-provider configuration presets.
- Add background job queue support for heavier workloads.
- Add tests for media processing, auth, and export behavior.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening an issue or pull request.

## License

This project is released under the [MIT License](LICENSE).
