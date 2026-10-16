# KAZAM VPS

独立部署的网易云听歌小工具，面向 `music.yeuxark.com`，并可对接个人主页 `yeuxark.com`。

## 功能一览

| 功能 | 说明 |
|------|------|
| 搜索 / 播放 / 歌词 / 下载 | 点一下就播；歌词跟着走，点一行跳过去 |
| **我的列表 · 今日电台** | 存在本机 `localStorage`，互不覆盖；拖动或 Alt+↑↓ 排序，「下一首播放」 |
| **解析** | 搜索框里粘贴歌曲 ID 或网易云链接，直接加入列表并播放 |
| **导入 / 导出** | 网易云歌单（替换或追加）、JSON / M3U / 文本 |
| **服务端缓存 · 曲库** | 管理员把音频落到服务器磁盘，CDN 过期仍可播 |
| 稳定 stream 地址 | `/api/stream/{id}`（电台请用这个，不要存 CDN 直链） |
| 嵌入播放器 | `/embed?id={id}` |
| 分享深链 | `/?play={id}` · `/?parse={id或链接}` · `/?q={关键词}` |

界面是「封面取色 · 沉浸式」：背景是当前封面的模糊大图，主色从封面里取；手机上列表在前、底部迷你播放条，点开是全屏播放页（可切歌词）。支持锁屏 / 耳机控制（Media Session）、刷新后从上次的位置接着听。快捷键：空格 播放/暂停 · ←/→ 上/下一首 · Shift+←/→ 快退/快进 10 秒 · ↑/↓ 音量 · M 静音 · L 歌词 · / 搜索。

## 快速启动（开发）

```bash
cd vps_version
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # 按需改 PUBLIC_BASE_URL
python app.py
# → http://127.0.0.1:8080
```

## Docker 部署

```bash
cd vps_version
docker compose up -d --build
```

- 应用：`http://VPS_IP:8080`
- 缓存卷：`kazam_cache` → 容器内 `/app/data/cache`
- 反代：参考 `nginx.conf.example`，域名 `music.yeuxark.com`

## 环境变量（节选）

见 `.env.example`：

- `PUBLIC_BASE_URL` — 对外根地址
- `CORS_ORIGINS` — 允许跨域的个人站域名
- `CACHE_DIR` — 服务端音频缓存目录
- `AUDIO_LEVEL` — 默认音质

---

## 用法

### 解析链接 / ID

在搜索框粘贴 `19723756` 或 `https://music.163.com/song?id=19723756`，回车：解析歌名、封面 → 加入「我的列表」→ 开始播放。

```http
GET /api/parse?q=19723756
GET /api/parse?q=https://music.163.com/song?id=19723756
```

返回含：`name` / `artist` / `cover` / `streamUrl` / `pageUrl` / `embedUrl` / `cached`

### 管理员与服务端缓存

连点左上角 KAZAM 五次，输入管理员密码（`.env` 的 `ADMIN_PASSWORD`）。之后：

1. 播放页的「缓存」或歌曲菜单里的「缓存到服务器」把音频写入 `data/cache/{id}.audio.*`，封面、歌词一并保存
2. 之后 `/api/stream/{id}` **优先读本地文件**（响应头 `X-Kazam-Cache: hit`）
3. 「曲库」标签列出所有已缓存曲目；右上角可管理上游、看 ChKSz 额度

```http
POST /api/cache/{id}             # 触发缓存（管理员）
GET  /api/cache/{id}/status      # 是否已缓存
GET  /api/cache/{id}/cover?size= # 缓存封面的 WebP 小图（160 / 320 / 480）
GET  /api/library                # 曲库列表
DELETE /api/cache/{id}           # 删除缓存（管理员）
```

### 性能相关

- 搜索结果在内存里缓存 10 分钟，歌词 6 小时；今日电台的关键词并行搜索
- 封面：网易图片按显示尺寸向网易 CDN 要（`param=NyN`）；`/api/cover-proxy?url=…&size=` 也会缩放并缓存到 `data/cache/_covers/`
- 未缓存的歌：一次 GET 直达 CDN 并透传 Range（不再先探测、HEAD），链接失效自动重新解析一次
- 同一首歌的上游查询合并成一次（播放器同时请求音频和元数据也只花一次额度）
- 静态文件带内容哈希 `?v=`，缓存一年；nginx 对 JSON / JS / CSS 开 gzip（见 `deploy/`）
- gunicorn 用 16 个线程（`deploy/kazam.service`）：每个正在播放的音频会占一个线程
- 可选：设置 `KAZAM_CACHE_ACCEL=/_kazam_cache/` 并在 nginx 加一个 `internal` 的 location（`alias` 到缓存目录），缓存音频改由 nginx 直接发送、不占线程。需要 nginx 能读到缓存目录——放开目录权限前先把 `.env` 改成 600

---

## 对接 yeuxark.com

### A. 电台：只存 ID，播放走 stream

```js
// 推荐：稳定地址，不要存 m801.music.126.net 直链
audio.src = `https://music.yeuxark.com/api/stream/${neteaseId}`;
```

### B. 后台「解析并填入」

```js
async function fillFromNetease(input) {
  const r = await fetch(
    "https://music.yeuxark.com/api/parse?q=" + encodeURIComponent(input)
  );
  const j = await r.json();
  if (!j.ok) throw new Error(j.error);
  // j.data.name / artist / cover / streamUrl
  document.querySelector("[name=title]").value = j.data.name;
  document.querySelector("[name=artist]").value = j.data.artist;
  document.querySelector("[name=cover]").value = j.data.cover;
  document.querySelector("[name=audio]").value = j.data.streamUrl;
  document.querySelector("[name=neteaseId]").value = j.data.id;
}
```

### C. 可选：解析后立刻服务端缓存（更稳）

```js
await fetch(`https://music.yeuxark.com/api/cache/${id}`, { method: "POST" });
// 之后 stream 走磁盘，不怕 CDN 过期
```

注意：缓存占 VPS 磁盘；公开站点请自行加鉴权/限流（当前版本未强制登录）。

### D. 嵌入迷你播放器

```html
<iframe
  src="https://music.yeuxark.com/embed?id=19723756"
  width="400" height="100"
  style="border:0;border-radius:8px;overflow:hidden"
  loading="lazy"
  allow="autoplay"
></iframe>
```

### E. CORS

默认允许：

- `https://yeuxark.com`
- `https://www.yeuxark.com`
- `https://music.yeuxark.com`

在 `.env` 的 `CORS_ORIGINS` 中追加即可。

---

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 + 缓存数量 |
| GET | `/api/parse?q=` | 解析 ID/链接 |
| GET | `/api/search?q=` | 搜索 |
| GET | `/api/song/{id}` | 元数据 + 稳定 URL |
| GET | `/api/resolve/{id}` | 轻量解析（电台） |
| GET | `/api/stream/{id}` | 播放流（缓存优先） |
| POST | `/api/cache/{id}` | 缓存到磁盘 |
| GET | `/api/library` | 服务端曲库 |
| GET | `/embed?id=` | 嵌入页 |

---

## 目录结构

```text
vps_version/
  app.py
  config.py
  requirements.txt
  Dockerfile
  docker-compose.yml
  nginx.conf.example
  static/          # CSS / JS
  templates/       # index + embed
  data/cache/      # 服务端音频缓存（运行时）
```

## 安全与合规提示

- 上游依赖第三方 `UPSTREAM_API`，可用性不保证
- 公网部署建议：反代 HTTPS、限流、可选访问密码
- 仅供个人学习/自用；注意版权与服务条款
