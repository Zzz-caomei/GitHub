# Security Policy

## Supported Versions

Security fixes are handled on the latest public version of the project.

## Reporting A Vulnerability

If you find a security issue, please do not open a public issue with exploit details.

Instead, contact the maintainer through GitHub with a private security report or a direct message. Include:

- A short description of the issue.
- Steps to reproduce.
- Potential impact.
- Affected files, routes, or configuration.
- Any suggested fix, if available.

## Sensitive Data

This project can process private videos, images, prompts, customer materials, and generated reports. Operators should treat the runtime directory as confidential application data.

Never commit or publish:

- `.env` files
- API keys or relay tokens
- Uploaded media
- SQLite databases
- Generated reports
- Export files
- Runtime logs
- Customer or project materials

## Deployment Notes

- Use HTTPS when exposing the app outside localhost.
- Set `AUTH_DISABLED=false` for public or semi-public deployments.
- Change `ADMIN_PASSWORD` before going online.
- Keep `SHOT_PLATFORM_HOME` outside the repository.
- Restrict filesystem and network access according to your deployment environment.

