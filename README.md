# KAZAM VPS

独立部署的网易云听歌小工具，面向 `music.yeuxark.com`，并可对接个人主页 `yeuxark.com`。

## 功能一览

| 功能 | 说明 |
|------|------|
| 搜索 / 播放 / 歌词 / 下载 | 基础听歌 |
| **本机播放列表** | `localStorage`，不同浏览器/用户互不干扰 |
| **✨ 解析填入** | 粘贴歌曲 ID 或网易云链接 → 自动填歌名、封面、稳定地址 |
| **💾 服务端缓存** | 把音频落到服务器磁盘，CDN 过期仍可播 |
| **服务端曲库** | 查看已缓存歌曲 |
| 稳定 stream 地址 | `/api/stream/{id}`（电台请用这个，不要存 CDN 直链） |
| 嵌入播放器 | `/embed?id={id}` |
| 分享深链 | `/?play={id}` · `/?parse={id或链接}` |

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

## 新功能用法

### 2. 一键解析填入

1. 打开 `music.yeuxark.com`
2. 点顶部 **「✨ 解析填入」**
3. 粘贴例如：
   - `19723756`
   - `https://music.163.com/song?id=19723756`
4. 自动：解析歌名/封面 → 写入本机列表 → 开始播放

API：

```http
GET /api/parse?q=19723756
GET /api/parse?q=https://music.163.com/song?id=19723756
```

返回含：`name` / `artist` / `cover` / `streamUrl` / `pageUrl` / `embedUrl` / `cached`

搜索框直接粘贴链接或纯 ID 也会走解析逻辑。

### 4. 歌曲 ID → 本地文件缓存

1. 播放任意歌曲
2. 点 **「💾 缓存到服务器」**
3. 音频写入 `data/cache/{id}.audio.*`，封面/歌词一并保存
4. 之后 `/api/stream/{id}` **优先读本地文件**（响应头 `X-Kazam-Cache: hit`）
5. 顶部 **「💾 服务端库」** 可浏览所有已缓存曲目

API：

```http
POST /api/cache/{id}          # 触发缓存
GET  /api/cache/{id}/status   # 是否已缓存
GET  /api/library             # 曲库列表
DELETE /api/cache/{id}        # 删除缓存
```

列表项上的 💾 按钮也可对单曲缓存。

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
