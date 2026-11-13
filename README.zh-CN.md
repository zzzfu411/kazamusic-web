# KAZAM Web

基于 Flask 自建的网页音乐播放器与音频流代理：支持多音源解析回退、服务端磁盘缓存、逐行同步歌词与可嵌入迷你播放器。

[English](README.md) · 简体中文

## 核心特性

- **多音源解析与自动回退** — 支持 `GD 音乐台`、`ChKSz`、`Meting`、`ORS`、`HiFLAC` 等后端配置，按 `GD → ChKSz → Meting` 顺序自动回退，合并并发重复请求，支持 `standard` 至 `lossless` / `jymaster` 音质档位。
- **服务端磁盘缓存** — 管理员触发缓存（`POST /api/cache/{id}`）后将音频、封面与歌词落盘至 `data/cache/`，使 `/api/stream/{id}` 不受上游 CDN 链接过期影响，并支持通过 Nginx `X-Accel-Redirect` 直接分发文件。
- **封面取色界面与本地歌单** — 动态提取封面主色与模糊背景，支持点击歌词跳转、Media Session 锁屏/耳机控制与全局快捷键；「我的列表」与「今日电台」独立保存在浏览器 `localStorage`，支持网易云歌单、JSON、M3U 与文本导入导出。
- **稳定流地址与嵌入组件** — 提供透传 HTTP `Range` 的 `/api/stream/{id}`、支持歌曲 ID 或网易云链接的 `/api/parse`、`/embed?id={id}` 迷你播放器，以及 `/?play=`、`/?parse=`、`/?q=` 分享深链。
- **出站安全防护** — 对上游重定向与 `/api/cover-proxy` 执行域名后缀白名单、`80`/`443` 端口限制与逐跳公网 IP 校验（`safe_fetch.py`），阻断内网探测。

## 快速开始

环境要求：**Python 3.12+**（或 **Docker**）。

```bash
git clone https://github.com/zzzfu411/kazamusic-web.git
cd kazamusic-web
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

启动后访问 `http://127.0.0.1:8080`；也可使用 Docker Compose 构建运行：

```bash
docker compose up -d --build
```

## 配置说明

启动前将 `.env.example` 复制为 `.env`。在页面左上角连续点击 5 次 `KAZAM` 标志并输入管理员密码，即可管理服务端缓存、上游源开关与配额状态。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | 监听地址与 HTTP 端口 |
| `PUBLIC_BASE_URL` | `https://music.yeuxark.com` | 用于生成分享链接与嵌入地址的公开根 URL |
| `CORS_ORIGINS` | `https://yeuxark.com,...` | 允许跨域访问的前端域名列表（逗号分隔） |
| `UPSTREAM_API` | `https://api.chksz.com/api` | ChKSz 主音源地址（配合 `CHKSZ_API_KEY` 使用） |
| `METING_APIS` | `https://api.qijieya.cn/meting/` | Meting 回退接口列表（逗号分隔） |
| `AUDIO_LEVEL` | `exhigh` | 默认音质（`standard`、`higher`、`exhigh`、`lossless`、`hires`、`jymaster`） |
| `CACHE_DIR` | `./data/cache` | 服务端音频、歌词与缩略图缓存目录 |
| `KAZAM_CACHE_ACCEL` | *（未设置）* | Nginx `X-Accel-Redirect` 内部路径前缀（如 `/_kazam_cache/`） |
| `KAZAM_UPSTREAM_PROXY` | *（未设置）* | 供受限机房访问上游的 HTTP `CONNECT` 中转地址 |
| `ADMIN_PASSWORD` | `change-me` | 管理员登录与服务端缓存写入密码 |

## 校验与运维文档

```bash
python qa_check.py  # 路由、解析器、SSRF 防护与 Flask 客户端自检
```

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — 生产目录权限、Nginx `X-Accel-Redirect`、`CONNECT` 中转代理（`deploy/relay/`）、完整 API 列表与外部站点对接示例
- [nginx.conf.example](nginx.conf.example) 与 [deploy/](deploy/) — Nginx 反代模板、systemd 服务单元（`kazam.service`）及中转配置

---

仅供个人学习与自托管使用，请遵守各音乐平台的服务条款与版权规定。
