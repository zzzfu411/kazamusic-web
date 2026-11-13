# 部署、运维与站点对接

本文档整理 KAZAM Web（`music.yeuxark.com`）的线上部署结构、Nginx / systemd 运维约定、上游回退与中转代理机制，以及外部站点（如 `yeuxark.com`）对接方式。

## 1. 线上目录、权限与进程管理

仓库 `deploy/` 目录提供生产环境参考配置：

- `deploy/kazam.service` — systemd 服务单元（Gunicorn `1` worker × `16` `gthread` 线程，监听 `127.0.0.1:8080`）
- `deploy/music.yeuxark.com.nginx` — Nginx 反向代理、登录限流与 `X-Accel-Redirect` 内部静态分发配置
- `deploy/relay/` — 面向受限机房 IP 的轻量 HTTP `CONNECT` 白名单中转服务（`relay.py` + `kazam-relay.service`）

### 目录与权限约定

- **缓存目录软链接**：生产音频缓存存放在 `/srv/kazam/cache`（属主 `yeux:www-data`，权限 `2750` 带 `setgid`，新建文件自动归属 `www-data` 组），项目内 `data/cache` 作为指向 `/srv/kazam/cache` 的符号链接。Nginx（`www-data`）仅读取该目录，`/home/yeux` 保持 `0750` 不向 `www-data` 开放。
- **不要通过 `CACHE_DIR` 迁移缓存**：后台运行时配置 `runtime.json`（含上游源与密钥设置）固定位于 `CACHE_DIR` 的父目录。若修改 `CACHE_DIR`，会导致进程找不到原有的 `runtime.json`；迁移磁盘路径请使用 `data/cache` 符号链接。
- **敏感文件权限与重启**：服务器上的 `.env`、`config.py`、`.env.example`、`deploy_test_admin.py` 权限应设为 `0600`。修改 `.env` 后需执行 `systemctl restart kazam`（环境变量由 systemd `EnvironmentFile` 加载，`kill -HUP` 不会重新读取）。

## 2. Nginx 加速与缓存机制

- **已缓存音频零线程分发（`X-Accel-Redirect`）**：在 `.env` 中设置 `KAZAM_CACHE_ACCEL=/_kazam_cache/`，并在 Nginx 中将 `internal` location `/_kazam_cache/` 指向 `/srv/kazam/cache/`。应用完成权限与命中检查后立即交由 Nginx 发送文件并处理 HTTP `Range` 拖动请求，播放期间不占用 Gunicorn 线程。
- **未缓存音频流转发**：单次 `GET` 直连上游 CDN 并透传 `Range`（不做前置 `HEAD` 探测），上游直链失效时自动重解析一次；未缓存流转发期间会占用 1 个 Gunicorn 线程。
- **合并请求与内存 TTL**：同一曲目的并发上游解析合并为单次请求（播放器同时拉取音频与元数据时不重复消耗额度）。搜索结果内存缓存 10 分钟，歌词缓存 6 小时，歌单（`/api/playlist/{id}`）缓存 30 分钟；封面代理 `/api/cover-proxy` 按需缩放并落盘缓存至 `data/cache/_covers/`。
- **管理端登录限速**：连点页面左上角 `KAZAM` 标志 5 次可唤出管理员密码框（对应 `.env` 中的 `ADMIN_PASSWORD`）。Nginx 对 `/api/admin/login` 按源地址限制为 `6r/m`（`burst=4 nodelay`），超限返回 `429`。

## 3. 多上游回退与 CONNECT 中转

- **回退顺序**：`auto` 模式与故障回退统一按 `GD 网易云 → chksz → Meting` 顺序尝试（优先完整音轨源，最后回退 Meting 试听/公共源）。若回退源仅返回音频直链而缺少曲目信息，优先沿用首选源的歌名/歌手/封面并以 Meting 补齐；数字曲目 ID 始终原样返回。
- **GD 签名与封面限流**：GD 签名所需的播放器版本号每 6 小时从 `player.js` 刷新一次（失败时默认值仅维持 5 分钟，签名被拒时最多每分钟重读一次）。`/api/gd/pic` 限制最大并发 4、单次超时 `3+5` 秒且不重试，失败结果负缓存 2 分钟。
- **跨机房中转（`KAZAM_UPSTREAM_PROXY`）**：GD 音乐台与 `flac.music.hi.cn` 可能拦截部分云厂商（如 Azure）机房 IP。可在未被拦截的服务器部署 `deploy/relay/`，仅放行音乐服务器源 IP（`RELAY_ALLOW_FROM`）、校验常量时间 Basic 认证（`kazam:<RELAY_TOKEN>`）、仅允许向白名单域名的 `80/443` 端口建立最多 64 条隧道，并以 systemd `DynamicUser` 无特权运行。

## 4. 出站安全边界（SSRF 防护）

- **音频流与跳转校验（`safe_fetch.py`）**：上游音频直链与 Meting 重定向逐跳校验（最多 5 次跳转），仅允许 `http` / `https`、禁止内嵌凭据，且域名解析出的所有 IP 必须为公网地址，阻断针对回环与内网网段的探测。
- **封面代理后缀白名单（`/api/cover-proxy`）**：仅允许 `app.py` `_COVER_SUFFIXES` 及已配置上游域名，严格按**域名后缀**匹配（例如 `qq.com` 不会放行 `qq.com.localtest.me`），仅限 `80/443` 端口，并在每次重定向时重新校验公网 IP。新增封面源时只需追加域名后缀。

## 5. HTTP API 参考

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查与已缓存曲目计数 |
| `GET` | `/api/parse?q=` | 解析网易云歌曲数字 ID 或单曲链接，返回元数据与稳定播放地址 |
| `GET` | `/api/search?q=` | 关键词搜索曲目 |
| `GET` | `/api/song/{id}` | 查询单曲元数据与稳定流地址 |
| `GET` | `/api/resolve/{id}` | 轻量音频解析（供外部电台调用） |
| `GET` | `/api/stream/{id}` | 音频播放流（命中服务端磁盘缓存时返回 `X-Kazam-Cache: hit`） |
| `GET` | `/api/library` | 列出服务端已缓存曲库 |
| `POST` | `/api/cache/{id}` | 触发曲目音频、封面与歌词落盘缓存（需管理员令牌） |
| `GET` | `/api/cache/{id}/status` | 查询指定曲目是否已缓存 |
| `GET` | `/api/cache/{id}/cover?size=` | 读取已缓存封面的 WebP 缩略图（`160` / `320` / `480`） |
| `DELETE` | `/api/cache/{id}` | 删除服务端曲目缓存（需管理员令牌） |
| `GET` | `/embed?id=` | 独立迷你嵌入播放器页面 |

## 6. 外部站点对接示例（`yeuxark.com`）

### 电台稳定流地址

不要直接持久化上游临时 CDN 链接（如 `m801.music.126.net`），统一使用 `/api/stream/{id}`：

```js
audio.src = `https://music.yeuxark.com/api/stream/${neteaseId}`;
```

### 后台解析并自动填表

```js
async function fillFromNetease(input) {
  const res = await fetch(
    "https://music.yeuxark.com/api/parse?q=" + encodeURIComponent(input)
  );
  const json = await res.json();
  if (!json.ok) throw new Error(json.error);
  document.querySelector("[name=title]").value = json.data.name;
  document.querySelector("[name=artist]").value = json.data.artist;
  document.querySelector("[name=cover]").value = json.data.cover;
  document.querySelector("[name=audio]").value = json.data.streamUrl;
  document.querySelector("[name=neteaseId]").value = json.data.id;
}
```

如需防止上游链接过期，可在管理员鉴权下调用 `POST /api/cache/${id}` 将音频持久化至服务器磁盘。

### 嵌入迷你播放器与跨域

```html
<iframe
  src="https://music.yeuxark.com/embed?id=19723756"
  width="400" height="100"
  style="border:0;border-radius:8px;overflow:hidden"
  loading="lazy"
  allow="autoplay"
></iframe>
```

跨域白名单通过 `.env` 中的 `CORS_ORIGINS`（逗号分隔）配置，默认包含 `https://yeuxark.com`、`https://www.yeuxark.com` 与 `https://music.yeuxark.com`。
