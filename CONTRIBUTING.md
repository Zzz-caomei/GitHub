# Contributing

Thanks for your interest in contributing to `duanjujingtoufantui.skill`.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8090
```

Open `http://127.0.0.1:8090`.

## Before Opening A Pull Request

- Keep generated runtime data out of Git.
- Do not commit `.env`, API keys, uploaded materials, SQLite databases, reports, exports, or logs.
- Keep changes focused on one purpose.
- Update `README.md` or `docs/` when behavior or setup changes.
- Test the flow manually with a small image or short video when touching upload, processing, auth, or export behavior.

## Good First Contributions

- Improve setup and deployment documentation.
- Add screenshots or demo walkthroughs that do not expose private assets.
- Add Docker or Compose support.
- Add tests for media handling, report generation, authentication, and exports.
- Improve error messages for API provider and relay failures.

## Issue Guidelines

When reporting a bug, include:

- Operating system and Python version.
- How the app was started.
- Relevant `.env` values with secrets removed.
- Media type and approximate file size.
- Expected behavior and actual behavior.
- Logs or error messages with sensitive data removed.

