# 环境变量速查

| 变量 | 作用范围 | 默认 | 备注 |
|---|---|---|---|
| `VITE_API_BASE` | 桌面 + Web | `http://127.0.0.1:62581` (desktop) / 空 (web=同源代理) | chayuan-server FastAPI |
| `VITE_BACKEND_URL` | Web dev 反代 | `http://127.0.0.1:62581` | vite proxy target |
| `VITE_LF_HOST` | 双端 | `http://127.0.0.1:3000` | Langfuse Web/ingest |
| `VITE_LF_PUBLIC_KEY` | 双端 | — | 缺失则禁用 Langfuse；secret 永不进客户端 |
| `VITE_LF_PROJECT_ID` | 双端 | — | 用于深链 |
| `VITE_APP_VERSION` | 双端 | `0.1.0` | release 标识 |
| `TAURI_DEV_HOST` | Tauri | — | LAN 联调时填本机 IP |

## Web 部署反代示例（nginx）

```nginx
server {
  listen 80;
  server_name client.chayuan.example;
  root /var/www/chayuan-client;
  index index.html;

  location / {
    try_files $uri /index.html;
  }

  # 后端反代
  location ~ ^/(auth|chat|knowledge_base|knowledge_source|tools|v1|api/v1/mcp_connections|governance|storage|modality|image_models|openapi|server|health|other|img|media)/ {
    proxy_pass http://127.0.0.1:62581;
    proxy_http_version 1.1;
    proxy_set_header Connection "";       # 保持 SSE 长连
    proxy_buffering off;                   # 关闭缓冲，token 实时
    proxy_read_timeout 24h;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  # Langfuse 反代（可选；同源访问免 CORS）
  location /lf/ {
    proxy_pass http://127.0.0.1:3000/;
    proxy_set_header Host $host;
  }
}
```
