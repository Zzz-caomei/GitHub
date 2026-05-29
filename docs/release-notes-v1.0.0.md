# v1.0.0 Release Notes

Initial public release of `duanjujingtoufantui.skill`.

## Highlights

- Upload reference videos or images.
- Extract video key frames and generate contact sheets.
- Automatically split video materials into rough shot segments.
- Call OpenAI Responses API or an OpenAI-compatible relay for visual analysis.
- Generate reusable video prompts covering shot style, camera movement, lighting, rhythm, positive keywords, and negative keywords.
- Save Markdown reports and reusable knowledge-base entries.
- Run as an internal no-login tool or enable account-based access control for project teams.
- Deploy locally or on a VPS with FastAPI, uvicorn, nginx, and systemd.

## Who This Is For

This release is designed for short drama teams, advertising teams, ecommerce listing teams, AI video creators, and internal production teams that need to convert reference materials into reusable video-generation prompts.

## Before You Run It

- Configure `.env` from `.env.example`.
- Keep `SHOT_PLATFORM_HOME` outside the repository for production use.
- Do not commit API keys, uploaded media, reports, databases, or logs.
- Use HTTPS and authentication when deploying outside a trusted network.

## Links

- README: `README.md`
- Deployment notes: `docs/deployment.md`
- License: `LICENSE`

