# 部署说明

## systemd 示例

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

## nginx 示例

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

## 注意事项

- `.env` 或 `/etc/video-prompt-workbench.env` 不要提交到仓库。
- `SHOT_PLATFORM_HOME` 建议指向仓库外部目录。
- 公网部署请启用 HTTPS 和访问控制。
