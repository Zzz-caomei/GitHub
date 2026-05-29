# Deployment Guide

This guide shows a simple VPS deployment for `duanjujingtoufantui.skill` with uvicorn, systemd, and nginx.

## Recommended Layout

```text
/opt/video-prompt-workbench/        Application code
/opt/shot-analysis-platform/        Runtime data, uploads, reports, database
/etc/video-prompt-workbench.env     Environment configuration
```

Keep runtime data and secrets outside the Git repository.

## 1. Prepare The App

```bash
cd /opt
git clone https://github.com/Zzz-caomei/GitHub.git video-prompt-workbench
cd /opt/video-prompt-workbench
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create the runtime directory:

```bash
mkdir -p /opt/shot-analysis-platform
```

## 2. Environment File

Create `/etc/video-prompt-workbench.env`:

```env
SHOT_PLATFORM_HOME=/opt/shot-analysis-platform
OPENAI_API_KEY=replace_with_your_key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.5
AUTH_DISABLED=false
MAX_UPLOAD_MB=300
ADMIN_USER=admin
ADMIN_PASSWORD=replace_with_a_strong_password
```

For trusted internal-only usage, `AUTH_DISABLED=true` is convenient. For public or semi-public deployments, use `AUTH_DISABLED=false`.

## 3. systemd Service

Create `/etc/systemd/system/video-prompt-workbench.service`:

```ini
[Unit]
Description=Video Prompt Workbench
After=network.target

[Service]
WorkingDirectory=/opt/video-prompt-workbench
EnvironmentFile=/etc/video-prompt-workbench.env
ExecStart=/opt/video-prompt-workbench/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8090
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
systemctl daemon-reload
systemctl enable video-prompt-workbench
systemctl start video-prompt-workbench
systemctl status video-prompt-workbench
```

## 4. nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name your-domain.example.com;

    client_max_body_size 500m;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

After configuring nginx, enable HTTPS with your preferred certificate workflow.

## 5. Operations Checklist

- Confirm `/health` returns a JSON response.
- Confirm uploads work with a small image or short video.
- Confirm `SHOT_PLATFORM_HOME` contains generated runtime data.
- Confirm `.env` and `/etc/video-prompt-workbench.env` are not committed.
- Rotate API keys if logs or shell history may have exposed them.
- Back up `data/database.sqlite` and the `knowledge_base/` directory if reports matter to your team.
- Clean old uploads and exports according to your retention policy.

## Troubleshooting

- `HTTP 401` from the model provider usually means the API key, relay key, or quota is invalid.
- `404` or provider errors around `/responses` can mean the relay does not support the Responses API path.
- Slow uploads or timeouts usually require a larger `client_max_body_size`, longer proxy timeouts, or smaller media files.
- Missing frame previews usually means OpenCV could not read the video container or codec.
