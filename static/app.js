/* KAZAM VPS frontend — localStorage playlist + parse + server cache */
(function () {
  "use strict";

  var STORAGE_KEY = "kazam.vps.v2";
  var THEME_KEY = "kazam.theme";
  var LEVEL_KEY = "kazam.audioLevel";
  var AUDIO_LEVELS = [
    "standard",
    "higher",
    "exhigh",
    "lossless",
    "hires",
    "jymaster",
  ];
  var LEVEL_LABELS = {
    standard: "标准",
    higher: "较高",
    exhigh: "极高 320k",
    lossless: "无损 FLAC",
    hires: "Hi-Res",
    jymaster: "💎 臻品母带",
  };
  /** 当前播放/下载音质，默认极高；可切到 chksz 臻品母带 */
  var audioLevel = "exhigh";
  var $ = function (s) {
    return document.querySelector(s);
  };
  var $$ = function (s) {
    return document.querySelectorAll(s);
  };

  function isValidLevel(v) {
    return AUDIO_LEVELS.indexOf(v) >= 0;
  }

  function loadAudioLevel() {
    try {
      var v = localStorage.getItem(LEVEL_KEY);
      if (isValidLevel(v)) audioLevel = v;
    } catch (e) {}
    var sel = $("#audioLevel");
    if (sel) sel.value = audioLevel;
  }

  function setAudioLevel(v, opts) {
    opts = opts || {};
    if (!isValidLevel(v)) return;
    var prev = audioLevel;
    audioLevel = v;
    try {
      localStorage.setItem(LEVEL_KEY, v);
    } catch (e) {}
    var sel = $("#audioLevel");
    if (sel && sel.value !== v) sel.value = v;
    if (opts.silent !== true) {
      toast("音质 → " + (LEVEL_LABELS[v] || v));
    }
    // 切换音质后若正在播，按新 level 重拉
    if (opts.reload !== false && prev !== v && curTrack && curTrack.id) {
      loadAndPlay(curTrack);
    }
  }

  function levelQuery(extra) {
    var parts = ["level=" + encodeURIComponent(audioLevel)];
    if (extra && typeof extra === "object") {
      Object.keys(extra).forEach(function (k) {
        if (extra[k] !== undefined && extra[k] !== null && extra[k] !== "") {
          parts.push(
            encodeURIComponent(k) + "=" + encodeURIComponent(String(extra[k]))
          );
        }
      });
    }
    return parts.join("&");
  }

  // ── theme (light / dark) ─────────────────────────────────────
  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark"
      ? "dark"
      : "light";
  }

  function applyTheme(theme, persist) {
    theme = theme === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", theme);
    var meta = document.getElementById("metaThemeColor");
    if (meta) meta.setAttribute("content", theme === "dark" ? "#030304" : "#facc15");
    var btn = $("#btnTheme");
    if (btn) {
      btn.title = theme === "dark" ? "切换到浅色模式" : "切换到黑夜模式";
      btn.setAttribute("aria-label", btn.title);
    }
    if (persist !== false) {
      try {
        localStorage.setItem(THEME_KEY, theme);
      } catch (e) {}
    }
  }

  function toggleTheme() {
    applyTheme(currentTheme() === "dark" ? "light" : "dark", true);
  }

  // sync button title after DOM ready
  applyTheme(currentTheme(), false);

  ["btnClearPl", "btnImportPl", "exportWrap", "btnReloadRadio", "btnClearRadio"].forEach(
    function (id) {
      var el = document.getElementById(id);
      if (el) el.style.display = "none";
    }
  );

  var audio = $("#audio");
  /** 自选列表（搜索/解析/歌单导入），与电台互不覆盖 */
  var playlist = [];
  /** 今日电台列表 */
  var radioPlaylist = [];
  var radioDate = "";
  /** 当前播放来源：my | radio */
  var playSource = "my";
  var curIdx = -1;
  var curTrack = null;
  var mode = "loop";
  var lyricData = [];
  var lyricCur = -1;
  var viewMode = "search";
  var searchCache = [];
  var libraryCache = [];
  var publicBase =
    (window.KAZAM_PUBLIC_BASE || "").replace(/\/$/, "") || location.origin;

  function activePlaylist() {
    return playSource === "radio" ? radioPlaylist : playlist;
  }

  function listByView() {
    if (viewMode === "radio") return radioPlaylist;
    return playlist;
  }

  // ── admin gate (地址 / 缓存到服务器) ─────────────────────────
  var ADMIN_TOKEN_KEY = "kazam.admin.token";
  var isAdmin = false;
  var adminToken = "";
  var logoClicks = 0;
  var logoClickTimer = null;

  function loadAdminSession() {
    try {
      adminToken = sessionStorage.getItem(ADMIN_TOKEN_KEY) || "";
      isAdmin = !!adminToken;
    } catch (e) {
      adminToken = "";
      isAdmin = false;
    }
    applyAdminUI();
  }

  function setAdminSession(token) {
    adminToken = token || "";
    isAdmin = !!adminToken;
    try {
      if (adminToken) sessionStorage.setItem(ADMIN_TOKEN_KEY, adminToken);
      else sessionStorage.removeItem(ADMIN_TOKEN_KEY);
    } catch (e) {}
    applyAdminUI();
    updateRightPanel();
    // refresh action buttons for current track
    if (curTrack) showActionButtons(true);
    else showActionButtons(false);
  }

  function applyAdminUI() {
    document.body.classList.toggle("admin-mode", isAdmin);
    var logo = $("#logoBtn");
    if (logo) logo.title = isAdmin ? "管理员模式" : "KAZAM";
    if (isAdmin) refreshChkszQuota();
    else renderChkszQuota(null);
  }

  function quotaLabel(j) {
    if (!j || !j.ok) return "今日已用 -- / 剩余 --";
    var used = j.used == null ? "--" : String(j.used);
    var left = j.remaining == null ? "--" : String(j.remaining);
    return "今日已用 " + used + " / 剩余 " + left;
  }

  function renderChkszQuota(j) {
    var chip = $("#chkszQuota");
    var box = $("#upQuotaText");
    var text = quotaLabel(j);
    var low = !!(j && j.remaining != null && Number(j.remaining) < 50);
    if (chip) {
      chip.textContent = text;
      chip.classList.toggle("is-low", low);
      var tip = "ChKSz 今日额度（点击刷新）";
      if (j && j.limit) tip += " · 免费上限 " + j.limit;
      if (j && j.paidRemaining) tip += " · 付费剩余 " + j.paidRemaining;
      if (j && !j.hasKey) tip += " · 未配置密钥";
      chip.title = tip;
    }
    if (box) box.textContent = text;
  }

  async function refreshChkszQuota() {
    if (!isAdmin || !adminToken) {
      renderChkszQuota(null);
      return;
    }
    try {
      var r = await fetch("/api/admin/chksz/usage", { headers: adminHeaders() });
      var j = await r.json();
      if (r.status === 401 || r.status === 403) {
        renderChkszQuota(null);
        return;
      }
      if (j && j.ok) renderChkszQuota(j);
    } catch (e) {
      /* keep last label */
    }
  }

  function adminHeaders() {
    var h = { "Content-Type": "application/json" };
    if (adminToken) h["X-Admin-Token"] = adminToken;
    return h;
  }

  async function tryAdminUnlock() {
    var pwd = prompt("管理员密码：");
    if (pwd === null) return;
    try {
      var r = await fetch("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pwd }),
      });
      var j = await r.json();
      if (!r.ok || !j.ok || !j.token) {
        toast("❌ 密码错误");
        return;
      }
      setAdminSession(j.token);
      toast("✅ 已进入管理员模式");
      refreshChkszQuota();
    } catch (e) {
      toast("❌ 登录失败");
    }
  }

  function exitAdmin() {
    setAdminSession("");
    if (viewMode === "library") {
      setActiveTab("search");
      updateRightPanel();
    }
    toast("已退出管理员");
  }

  // ── persistence ──────────────────────────────────────────────
  function loadState() {
    try {
      // 兼容旧版 v1 存储
      var raw =
        localStorage.getItem(STORAGE_KEY) ||
        localStorage.getItem("kazam.vps.v1");
      if (!raw) return;
      var s = JSON.parse(raw);
      if (Array.isArray(s.playlist)) playlist = s.playlist;
      if (Array.isArray(s.radioPlaylist)) radioPlaylist = s.radioPlaylist;
      if (s.radioDate) radioDate = s.radioDate;
      if (s.playSource === "radio" || s.playSource === "my") {
        playSource = s.playSource;
      }
      if (typeof s.curIdx === "number") curIdx = s.curIdx;
      if (s.mode) mode = s.mode;
      if (typeof s.volume === "number" && !isIOSDevice()) {
        audio.volume = Math.min(1, Math.max(0, s.volume));
      }
      if (s.muted) {
        audio.muted = true;
      }
      // 校正 curIdx 不越界
      var q = activePlaylist();
      if (curIdx >= q.length) curIdx = q.length ? 0 : -1;
    } catch (e) {
      console.warn("loadState failed", e);
    }
  }

  function saveState() {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          playlist: playlist,
          radioPlaylist: radioPlaylist,
          radioDate: radioDate,
          playSource: playSource,
          curIdx: curIdx,
          mode: mode,
          volume: audio.volume,
          muted: audio.muted,
          updatedAt: Date.now(),
        })
      );
    } catch (e) {
      console.warn("saveState failed", e);
    }
  }

  var saveTimer;
  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveState, 200);
  }

  // ── helpers ──────────────────────────────────────────────────
  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmt(s) {
    if (!s || !isFinite(s)) return "0:00";
    s = Math.floor(s);
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  var tt;
  function toast(m) {
    var e = $("#toast");
    e.textContent = m;
    e.classList.add("show");
    clearTimeout(tt);
    tt = setTimeout(function () {
      e.classList.remove("show");
    }, 2200);
  }

  async function api(p, opts) {
    var r = await fetch(p, opts || {});
    return r.json();
  }

  function coverSrc(url) {
    if (!url) return "";
    if (url.indexOf("/api/") === 0) return url;
    return "/api/cover-proxy?url=" + encodeURIComponent(url);
  }

  function setActiveTab(tab) {
    viewMode = tab;
    ["search", "playlist", "radio", "library", "lyrics"].forEach(function (t) {
      var el = document.getElementById(
        "tab" + t.charAt(0).toUpperCase() + t.slice(1)
      );
      if (el) el.classList.toggle("active", tab === t);
    });
    // 工具栏：自选可清空/导入；导出在自选+电台；电台可重新加载
    var toolsMy = ["btnClearPl", "btnImportPl"];
    var toolsRadio = ["btnReloadRadio"];
    toolsMy.forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.style.display = tab === "playlist" ? "" : "none";
    });
    toolsRadio.forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.style.display = tab === "radio" ? "" : "none";
    });
    var exportWrap = document.getElementById("exportWrap") || document.getElementById("btnExportPl");
    if (exportWrap) {
      exportWrap.style.display = tab === "playlist" || tab === "radio" ? "" : "none";
    }
    if (tab !== "playlist" && tab !== "radio") closeExportMenu();
  }

  // 模式图标：Twitter/Twemoji 风格 SVG，全端一致
  var MODE_ICON_SRC = {
    shuffle: "/static/icons/shuffle-tracks-button-twitter.svg",
    loop: "/static/icons/repeat-button-twitter.svg",
    "repeat-one": "/static/icons/repeat-single-button-twitter.svg",
  };
  var MODE_TITLES = {
    shuffle: "随机播放",
    loop: "列表循环",
    "repeat-one": "单曲循环",
  };

  function applyModeUI() {
    var btn = $("#btnMode");
    if (!btn) return;
    var key = mode === "shuffle" || mode === "repeat-one" ? mode : "loop";
    if (key === "loop") mode = "loop";
    var src = MODE_ICON_SRC[key];
    var title = MODE_TITLES[key];
    btn.innerHTML =
      '<img class="mode-emoji" src="' +
      src +
      '?v=4" alt="" width="22" height="22" draggable="false">';
    btn.title = title;
    // Native loop: 单曲循环不再重新请求 /api/stream，避免反复扣额度
    if (audio) audio.loop = mode === "repeat-one";
  }

  // iOS Safari ignores HTMLMediaElement.volume (always 1); only mute works
  function isIOSDevice() {
    var ua = navigator.userAgent || "";
    return (
      /iPad|iPhone|iPod/.test(ua) ||
      (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
    );
  }

  function syncVolUI() {
    var pct = Math.round((audio.muted ? 0 : audio.volume) * 100);
    var slider = $("#volSlider");
    var label = $("#volPct");
    if (slider && document.activeElement !== slider) {
      slider.value = String(Math.round(audio.volume * 100));
    }
    if (label) label.textContent = (audio.muted ? 0 : pct) + "%";
    var vb = $("#btnVol");
    if (vb) vb.textContent = audio.muted || audio.volume === 0 ? "🔇" : "🔊";
  }

  function setVolume(v, opts) {
    opts = opts || {};
    v = Math.min(1, Math.max(0, v));
    if (isIOSDevice() && !opts.fromMute && v !== audio.volume) {
      // Software volume unsupported on iOS — tip once
      var tip = $("#volIosTip");
      if (tip) tip.hidden = false;
      if (!opts.silent) {
        toast("iPhone/iPad 请用侧边音量键调节");
      }
      return;
    }
    audio.volume = v;
    if (v > 0 && audio.muted) audio.muted = false;
    syncVolUI();
    scheduleSave();
  }

  function bumpVolume(delta) {
    if (isIOSDevice()) {
      var tip = $("#volIosTip");
      if (tip) tip.hidden = false;
      toast("iPhone/iPad 请用侧边音量键调节");
      return;
    }
    setVolume(audio.volume + delta);
  }

  function setCacheBadge(on) {
    var b = $("#npCacheBadge");
    if (!b) return;
    // only meaningful / visible in admin mode
    b.classList.toggle("is-on", !!(on && isAdmin));
  }

  function showActionButtons(show) {
    ["btnDlSong", "btnDlLyric", "btnShare"].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.style.display = show ? "inline-flex" : "none";
      el.classList.toggle("is-visible", !!show);
    });
    // admin-only: 地址 / 缓存到服务器
    ["btnCopyUrl", "btnCacheSong"].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      var vis = !!(show && isAdmin);
      el.classList.toggle("is-visible", vis);
      // non-admin always hidden via .admin-only CSS
      if (!isAdmin) el.classList.remove("is-visible");
    });
  }

  // ── render ───────────────────────────────────────────────────
  function updateRightPanel() {
    var body = $("#rpBody");
    body.className = "rp-body";

    if (viewMode === "search") {
      $("#rpTitle").textContent = "搜 索 结 果";
      if (!searchCache.length) {
        body.innerHTML =
          '<div class="empty"><div class="icon">🔍</div>输入关键词搜索' +
          '<div class="hint">双击播放 · 单击加入列表</div></div>';
        $("#rpCount").textContent = "—";
      } else {
        body.innerHTML =
          '<div class="card-grid">' + searchCache.map(cardHTML).join("") + "</div>";
        $("#rpCount").textContent = searchCache.length + " hits";
        bindCards(searchCache);
      }
    } else if (viewMode === "playlist" || viewMode === "radio") {
      var isRadioView = viewMode === "radio";
      var list = isRadioView ? radioPlaylist : playlist;
      var listSource = isRadioView ? "radio" : "my";
      $("#rpTitle").textContent = isRadioView ? "今 日 电 台" : "我 的 列 表";
      if (!list.length) {
        body.innerHTML = isRadioView
          ? '<div class="empty"><div class="icon">📻</div>电台为空' +
            '<div class="hint">点顶部「今日电台」生成今日歌单<br>不会覆盖你的自选列表</div></div>'
          : '<div class="empty"><div class="icon">📋</div>列表为空' +
            '<div class="hint">双击搜索结果添加歌曲</div></div>';
        $("#rpCount").textContent = "0";
      } else {
        body.innerHTML = list
          .map(function (t, i) {
            var playingHere =
              playSource === listSource && i === curIdx ? " active" : "";
            var badge = t.cached ? ' <span class="mini-badge">缓存</span>' : "";
            var cacheBtn = isAdmin
              ? '<button type="button" class="pl-cache admin-only" data-idx="' +
                i +
                '" title="缓存到服务器">💾</button>'
              : "";
            var upDis = i === 0 ? " disabled" : "";
            var downDis = i === list.length - 1 ? " disabled" : "";
            return (
              '<div class="pl-item' +
              playingHere +
              '" data-idx="' +
              i +
              '" data-source="' +
              listSource +
              '" tabindex="0"><span class="pl-handle" draggable="true" title="拖动排序" aria-label="拖动排序">⋮⋮</span><span class="idx">' +
              (i + 1) +
              '</span><span class="name">' +
              esc(t.name) +
              " — " +
              esc(t.artist) +
              (isAdmin ? badge : "") +
              '</span><button type="button" class="pl-move pl-up" data-idx="' +
              i +
              '" title="上移"' +
              upDis +
              '">↑</button><button type="button" class="pl-move pl-down" data-idx="' +
              i +
              '" title="下移"' +
              downDis +
              '">↓</button>' +
              cacheBtn +
              '<button type="button" class="pl-dl" data-idx="' +
              i +
              '" title="下载">⬇</button><button type="button" class="pl-del" data-idx="' +
              i +
              '" title="移除">×</button></div>'
            );
          })
          .join("");
        var extra =
          isRadioView && radioDate ? " · " + radioDate : "";
        $("#rpCount").textContent = list.length + extra;
        bindPlaylistItems(listSource);
      }
    } else if (viewMode === "library") {
      if (!isAdmin) {
        setActiveTab("search");
        updateRightPanel();
        return;
      }
      $("#rpTitle").textContent = "服 务 端 曲 库";
      if (!libraryCache.length) {
        body.innerHTML =
          '<div class="empty"><div class="icon">💾</div>暂无缓存' +
          '<div class="hint">播放后点「缓存到服务器」</div></div>';
        $("#rpCount").textContent = "0";
      } else {
        body.innerHTML =
          '<div class="card-grid">' +
          libraryCache.map(cardHTML).join("") +
          "</div>";
        $("#rpCount").textContent = libraryCache.length + " 首";
        bindCards(libraryCache);
      }
    } else {
      $("#rpTitle").textContent = "歌 词";
      body.className = "rp-body lyrics-pad";
      if (!lyricData.length) {
        body.innerHTML =
          '<div class="empty"><div class="icon">🎤</div>暂无歌词<div class="hint">开始播放后显示</div></div>';
        $("#rpCount").textContent = "—";
      } else {
        body.innerHTML = lyricData
          .map(function (l, i) {
            var cls = i === lyricCur ? " active" : "";
            return (
              '<div class="lyric-line' +
              cls +
              '"><div class="orig">' +
              esc(l.orig) +
              "</div>" +
              (l.tran ? '<div class="tran">' + esc(l.tran) + "</div>" : "") +
              "</div>"
            );
          })
          .join("");
        $("#rpCount").textContent = lyricData.length + " 句";
      }
    }
  }

  function cardHTML(t) {
    var d = t.duration;
    var ds = d
      ? Math.floor(d / 60000) +
        ":" +
        String(Math.floor((d / 1000) % 60)).padStart(2, "0")
      : "--:--";
    var co = t.cover ? coverSrc(t.cover) : "";
    var thumb = co
      ? '<img src="' +
        co +
        '" loading="lazy" onerror="this.parentElement.innerHTML=\'♪\'">'
      : "♪";
    var badge = t.cached
      ? '<span class="card-badge" title="服务器已缓存">💾</span>'
      : "";
    return (
      '<div class="card" data-id="' +
      t.id +
      '"><div class="thumb-wrap">' +
      thumb +
      "</div>" +
      badge +
      '<button type="button" class="card-dl" data-id="' +
      t.id +
      '" title="下载">⬇</button><div class="info"><div class="title">' +
      esc(t.name) +
      '</div><div class="sub">' +
      esc(t.artist) +
      '</div><div class="sub" style="font-size:10px">' +
      ds +
      " · " +
      esc(t.album || "") +
      (t.cached ? " · 已缓存" : "") +
      "</div></div></div>"
    );
  }

  function bindCards(source) {
    source = source || searchCache;
    $$(".card").forEach(function (c) {
      c.ondblclick = function () {
        playById(c.dataset.id, source);
      };
      c.onclick = function () {
        addToListFromSource(c.dataset.id, source);
      };
    });
    $$(".card-dl").forEach(function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        var t = source.find(function (x) {
          return String(x.id) === String(btn.dataset.id);
        });
        if (t) downloadSong(t);
      };
    });
  }

  function moveTrack(source, fromIdx, toIdx) {
    source = source === "radio" ? "radio" : "my";
    var list = source === "radio" ? radioPlaylist : playlist;
    var n = list.length;
    if (n < 2) return false;
    fromIdx = parseInt(fromIdx, 10);
    toIdx = parseInt(toIdx, 10);
    if (
      !isFinite(fromIdx) ||
      !isFinite(toIdx) ||
      fromIdx === toIdx ||
      fromIdx < 0 ||
      toIdx < 0 ||
      fromIdx >= n ||
      toIdx >= n
    ) {
      return false;
    }
    var item = list.splice(fromIdx, 1)[0];
    list.splice(toIdx, 0, item);
    if (source === "radio") radioPlaylist = list;
    else playlist = list;

    var playingThis = playSource === (source === "radio" ? "radio" : "my");
    if (playingThis && curIdx >= 0) {
      if (curIdx === fromIdx) {
        curIdx = toIdx;
      } else if (fromIdx < curIdx && toIdx >= curIdx) {
        curIdx--;
      } else if (fromIdx > curIdx && toIdx <= curIdx) {
        curIdx++;
      }
    }
    scheduleSave();
    if (viewMode === "playlist" || viewMode === "radio") {
      updateRightPanel();
    }
    return true;
  }

  function clearPlDropMarks() {
    $$(".pl-item").forEach(function (el) {
      el.classList.remove("drop-before", "drop-after", "dragging");
    });
  }

  var suppressPlClick = false;
  var plDropHover = null;

  function bindPlaylistItems(source) {
    source = source || "my";
    var list = source === "radio" ? radioPlaylist : playlist;
    $$(".pl-item").forEach(function (el) {
      el.onclick = function () {
        if (suppressPlClick) return;
        var idx = parseInt(el.dataset.idx, 10);
        var src = el.dataset.source || source;
        playSource = src === "radio" ? "radio" : "my";
        curIdx = idx;
        var q = activePlaylist();
        if (q[curIdx]) loadAndPlay(q[curIdx]);
        scheduleSave();
        updateRightPanel();
      };
    });
    $$(".pl-dl").forEach(function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        var t = list[parseInt(btn.dataset.idx, 10)];
        if (t) downloadSong(t);
      };
    });
    $$(".pl-del").forEach(function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        removeFromList(source, parseInt(btn.dataset.idx, 10));
      };
    });
    $$(".pl-cache").forEach(function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        var t = list[parseInt(btn.dataset.idx, 10)];
        if (t) cacheTrack(t);
      };
    });
    $$(".pl-up").forEach(function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        var i = parseInt(btn.dataset.idx, 10);
        moveTrack(source, i, i - 1);
      };
    });
    $$(".pl-down").forEach(function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        var i = parseInt(btn.dataset.idx, 10);
        moveTrack(source, i, i + 1);
      };
    });
    $$(".pl-handle").forEach(function (h) {
      h.onclick = function (e) {
        e.stopPropagation();
      };
      h.addEventListener("dragstart", function (e) {
        var row = h.closest(".pl-item");
        if (!row) return;
        var idx = parseInt(row.dataset.idx, 10);
        e.stopPropagation();
        if (e.dataTransfer) {
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", String(idx));
        }
        row.classList.add("dragging");
        h._dragFrom = idx;
        h._dragSource = row.dataset.source || source;
      });
      h.addEventListener("dragend", function () {
        clearPlDropMarks();
      });
    });
    $$(".pl-item").forEach(function (el) {
      el.addEventListener("dragover", function (e) {
        if (!e.dataTransfer) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        var rect = el.getBoundingClientRect();
        var after = e.clientY > rect.top + rect.height / 2;
        if (plDropHover && plDropHover !== el) {
          plDropHover.classList.remove("drop-before", "drop-after");
        }
        plDropHover = el;
        el.classList.toggle("drop-after", after);
        el.classList.toggle("drop-before", !after);
      });
      el.addEventListener("dragleave", function (e) {
        var rel = e.relatedTarget;
        if (rel && el.contains(rel)) return;
        el.classList.remove("drop-before", "drop-after");
        if (plDropHover === el) plDropHover = null;
      });
      el.addEventListener("drop", function (e) {
        e.preventDefault();
        e.stopPropagation();
        var from = parseInt(
          (e.dataTransfer && e.dataTransfer.getData("text/plain")) || "-1",
          10
        );
        var hover = parseInt(el.dataset.idx, 10);
        var src = el.dataset.source || source;
        var rect = el.getBoundingClientRect();
        var after = e.clientY > rect.top + rect.height / 2;
        var target = after ? hover + 1 : hover;
        if (from < target) target--;
        var n = (src === "radio" ? radioPlaylist : playlist).length;
        if (target > n - 1) target = n - 1;
        if (target < 0) target = 0;
        suppressPlClick = true;
        setTimeout(function () {
          suppressPlClick = false;
        }, 250);
        plDropHover = null;
        clearPlDropMarks();
        moveTrack(src, from, target);
      });
    });
  }

  function updateCardStates() {
    $$(".card").forEach(function (c) {
      c.classList.toggle(
        "playing",
        c.dataset.id == (curTrack ? curTrack.id : "")
      );
    });
  }

  // ── Feature 2: parse ID / link ────────────────────────────────
  async function resolveAndFill(raw, opts) {
    opts = opts || {};
    var q = (raw || "").trim();
    if (!q) {
      toast("请输入歌曲 ID 或链接");
      return null;
    }
    toast("解析中…");
    var r = await api("/api/parse?q=" + encodeURIComponent(q));
    if (!r.ok || !r.data) {
      toast("❌ 解析失败: " + (r.error || "unknown"));
      return null;
    }
    var t = {
      id: r.data.id,
      name: r.data.name || String(r.data.id),
      artist: r.data.artist || "",
      album: r.data.album || "",
      cover: r.data.cover || "",
      duration: r.data.duration || 0,
      cached: !!r.data.cached,
      streamUrl: r.data.streamUrl,
      pageUrl: r.data.pageUrl,
      embedUrl: r.data.embedUrl,
    };
    // 写入「自选列表」，不影响电台
    playSource = "my";
    var idx = playlist.findIndex(function (x) {
      return String(x.id) === String(t.id);
    });
    if (idx >= 0) {
      playlist[idx] = Object.assign({}, playlist[idx], t);
      curIdx = idx;
    } else {
      playlist.push(t);
      curIdx = playlist.length - 1;
    }
    scheduleSave();
    searchCache = [t];
    setActiveTab(opts.staySearch ? "search" : "playlist");
    updateRightPanel();
    toast("✅ 已填入自选列表: " + t.name + " — " + t.artist);
    if (opts.play !== false) {
      await loadAndPlay(playlist[curIdx]);
    }
    if (opts.cache && isAdmin) {
      await cacheTrack(playlist[curIdx]);
    }
    return t;
  }

  function promptResolve() {
    var pre = ($("#searchInput").value || "").trim();
    var raw = prompt(
      "粘贴网易云歌曲链接或纯数字 ID：\n（将自动拉取歌名、封面，并加入你的列表）",
      pre
    );
    if (raw === null) return;
    resolveAndFill(raw, { play: true });
  }

  // ── Feature 4: server cache ──────────────────────────────────
  async function cacheTrack(track) {
    if (!track || !track.id) return;
    if (!isAdmin || !adminToken) {
      toast("需要管理员权限");
      return;
    }
    toast("正在缓存到服务器…");
    try {
      var resp = await fetch("/api/cache/" + track.id + "?" + levelQuery(), {
        method: "POST",
        headers: adminHeaders(),
      });
      var r = await resp.json();
      if (resp.status === 401 || resp.status === 403) {
        exitAdmin();
        toast("❌ 管理员登录已失效");
        return;
      }
      if (!r.ok) {
        toast("❌ 缓存失败: " + (r.error || ""));
        return;
      }
      track.cached = true;
      if (r.data) {
        track.name = r.data.name || track.name;
        track.artist = r.data.artist || track.artist;
        track.cover = r.data.cover || track.cover;
      }
      // sync both lists
      [playlist, radioPlaylist].forEach(function (list) {
        list.forEach(function (p, i) {
          if (String(p.id) === String(track.id)) {
            list[i] = Object.assign({}, p, {
              cached: true,
              name: track.name,
              artist: track.artist,
              cover: track.cover,
            });
          }
        });
      });
      if (curTrack && String(curTrack.id) === String(track.id)) {
        curTrack.cached = true;
        setCacheBadge(true);
        var q = $("#npQuality");
        if (q && q.style.display !== "none") {
          /* keep quality label */
        }
      }
      scheduleSave();
      toast("💾 已缓存: " + track.name);
      if (viewMode === "library" || viewMode === "playlist") {
        if (viewMode === "library") await loadLibrary();
        else updateRightPanel();
      }
      updateCardStates();
    } catch (e) {
      toast("❌ 缓存异常");
    }
  }

  async function loadLibrary() {
    try {
      var r = await api("/api/library");
      libraryCache = r.ok && r.data ? r.data : [];
    } catch (e) {
      libraryCache = [];
    }
    if (viewMode === "library") updateRightPanel();
  }

  // ── playlist ops ─────────────────────────────────────────────
  function findInSources(id) {
    var pools = [searchCache, libraryCache, playlist, radioPlaylist];
    for (var i = 0; i < pools.length; i++) {
      var t = pools[i].find(function (x) {
        return String(x.id) === String(id);
      });
      if (t) return t;
    }
    return null;
  }

  function addToListFromSource(id, source) {
    var t =
      (source &&
        source.find(function (x) {
          return String(x.id) === String(id);
        })) ||
      findInSources(id);
    if (!t) return;
    // 自选列表专用；电台列表不由此添加
    if (
      !playlist.find(function (x) {
        return String(x.id) === String(id);
      })
    ) {
      playlist.push(Object.assign({}, t));
      toast("+ " + t.name + "（自选列表）");
      scheduleSave();
    } else {
      toast("已在自选列表中");
    }
    if (viewMode === "playlist") updateRightPanel();
  }

  function playById(id, source) {
    addToListFromSource(id, source);
    playSource = "my";
    curIdx = playlist.findIndex(function (x) {
      return String(x.id) === String(id);
    });
    if (curIdx >= 0) loadAndPlay(playlist[curIdx]);
  }

  function removeFromList(source, idx) {
    var list = source === "radio" ? radioPlaylist : playlist;
    if (idx < 0 || idx >= list.length) return;
    toast("已移除: " + list[idx].name);
    list.splice(idx, 1);
    if (source === "radio") radioPlaylist = list;
    else playlist = list;

    if (playSource === (source === "radio" ? "radio" : "my")) {
      if (curIdx === idx) {
        audio.pause();
        audio.removeAttribute("src");
        curTrack = null;
        curIdx = -1;
        resetNowPlaying();
      } else if (curIdx > idx) {
        curIdx--;
      }
    }
    scheduleSave();
    updateRightPanel();
    updateCardStates();
  }

  function clearPlaylist() {
    if (!playlist.length) return;
    if (!confirm("清空「我的列表」？今日电台不会受影响。")) return;
    playlist = [];
    if (playSource === "my") {
      curIdx = -1;
      curTrack = null;
      audio.pause();
      audio.removeAttribute("src");
      lyricData = [];
      lyricCur = -1;
      resetNowPlaying();
    }
    scheduleSave();
    updateRightPanel();
    updateCardStates();
    toast("自选列表已清空");
  }

  function clearRadioPlaylist() {
    if (!radioPlaylist.length) return;
    if (!confirm("清空「今日电台」列表？自选列表不会受影响。")) return;
    radioPlaylist = [];
    radioDate = "";
    if (playSource === "radio") {
      curIdx = -1;
      curTrack = null;
      audio.pause();
      audio.removeAttribute("src");
      lyricData = [];
      lyricCur = -1;
      resetNowPlaying();
      playSource = "my";
    }
    scheduleSave();
    updateRightPanel();
    updateCardStates();
    toast("电台列表已清空");
  }

  function pad2(n) {
    return n < 10 ? "0" + n : String(n);
  }

  function exportStamp() {
    var d = new Date();
    return "" + d.getFullYear() + pad2(d.getMonth() + 1) + pad2(d.getDate());
  }

  function closeExportMenu() {
    var menu = $("#exportMenu");
    var btn = $("#btnExportPl");
    if (menu) menu.hidden = true;
    if (btn) btn.setAttribute("aria-expanded", "false");
  }

  function toggleExportMenu() {
    var menu = $("#exportMenu");
    var btn = $("#btnExportPl");
    if (!menu) return;
    if (menu.hidden) {
      menu.hidden = false;
      if (btn) btn.setAttribute("aria-expanded", "true");
    } else {
      closeExportMenu();
    }
  }

  function buildExportTracks() {
    var src = viewMode === "radio" ? radioPlaylist : playlist;
    return src.map(function (t) {
      return {
        id: t.id,
        name: t.name || String(t.id),
        artist: t.artist || "",
        album: t.album || "",
        cover: t.cover || "",
        duration: t.duration || 0,
      };
    });
  }

  function downloadText(filename, mime, text) {
    var blob = new Blob([text], { type: mime });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function exportCurrentList(fmt) {
    var tracks = buildExportTracks();
    if (!tracks.length) {
      toast(viewMode === "radio" ? "电台列表为空，无法导出" : "自选列表为空，无法导出");
      return;
    }
    var kind = viewMode === "radio" ? "radio" : "my";
    var stamp = exportStamp();
    var filename;
    var mime;
    var text;
    if (fmt === "m3u") {
      filename = "kazam-" + kind + "-" + stamp + ".m3u";
      mime = "audio/x-mpegurl";
      var lines = ["#EXTM3U"];
      tracks.forEach(function (t) {
        var sec = t.duration ? Math.max(0, Math.round(Number(t.duration) / 1000)) : -1;
        lines.push(
          "#EXTINF:" + sec + "," + (t.artist || "") + " - " + (t.name || String(t.id))
        );
        lines.push(publicBase + "/api/stream/" + t.id);
      });
      text = lines.join("\n") + "\n";
    } else if (fmt === "txt") {
      filename = "kazam-" + kind + "-" + stamp + ".txt";
      mime = "text/plain;charset=utf-8";
      text =
        tracks
          .map(function (t, i) {
            return i + 1 + ". " + (t.name || String(t.id)) + " — " + (t.artist || "");
          })
          .join("\n") + "\n";
    } else {
      filename = "kazam-" + kind + "-" + stamp + ".json";
      mime = "application/json";
      text = JSON.stringify(
        {
          app: "kazam",
          version: 2,
          kind: kind,
          exportedAt: Date.now(),
          count: tracks.length,
          playlist: tracks,
        },
        null,
        2
      );
    }
    downloadText(filename, mime, text);
    closeExportMenu();
    toast("已导出 " + tracks.length + " 首");
  }

  function importPlaylist(file) {
    var reader = new FileReader();
    reader.onload = function () {
      try {
        var data = JSON.parse(reader.result);
        // v1: 裸数组或 {playlist}; v2: {app,version,kind,playlist}
        var list = Array.isArray(data)
          ? data
          : data && Array.isArray(data.playlist)
            ? data.playlist
            : null;
        if (!list) throw new Error("invalid");
        var n = 0;
        list.forEach(function (t) {
          if (!t || !t.id) return;
          if (
            !playlist.find(function (x) {
              return String(x.id) === String(t.id);
            })
          ) {
            playlist.push({
              id: t.id,
              name: t.name || String(t.id),
              artist: t.artist || "",
              album: t.album || "",
              cover: t.cover || "",
              duration: t.duration || 0,
              cached: !!t.cached,
            });
            n++;
          }
        });
        scheduleSave();
        setActiveTab("playlist");
        updateRightPanel();
        toast("导入 " + n + " 首");
      } catch (e) {
        toast("导入失败");
      }
    };
    reader.readAsText(file);
  }

  function resetNowPlaying() {
    $("#npTitle").textContent = "KAZAM!";
    $("#npArtist").textContent = "等待播放";
    $("#npQuality").style.display = "none";
    setCacheBadge(false);
    showActionButtons(false);
    showNpCover("");
  }

  function renderQuality(data) {
    if (!data) {
      $("#npQuality").style.display = "none";
      return;
    }
    var qm = {
      jymaster: "💎臻品母带",
      lossless: "🔵无损",
      hires: "🟣Hi-Res",
      exhigh: "🟢极高",
      higher: "🟡较高",
      standard: "⚪标准",
      meting: "🟡在线",
    };
    var lv = data.level || "";
    var src = data.source || "";
    var label =
      qm[lv] ||
      (lv && lv !== "meting" ? "🎵 " + lv : "") ||
      (data.cached ? "💾 本地缓存" : "HQ");
    // source 提示：管理员可见上游名
    if (src === "meting" && isAdmin) {
      label = (qm[lv] && lv !== "meting" ? qm[lv] : "🟡在线") + "·Meting";
    } else if (src === "chksz" && isAdmin) {
      label = (qm[lv] || "🟢主源") + "·主源";
    } else if (lv === "meting") {
      label = "🟡在线";
    }
    var brNum = data.br ? Math.round(Number(data.br) / 1000) : 0;
    // br sometimes already in kbps (< 10000)
    if (data.br && Number(data.br) > 0 && Number(data.br) < 10000) {
      brNum = Math.round(Number(data.br));
    }
    var br = brNum > 0 ? brNum + "kbps" : "";
    var sz = data.size
      ? " · " + (Number(data.size) / 1024 / 1024).toFixed(1) + "MB"
      : "";
    var typ = data.type && data.type !== "mp3" ? " · " + data.type : "";
    var cacheTag = data.cached ? " · 本地" : "";
    $("#npQuality").textContent =
      label + (br ? " · " + br : "") + sz + typ + cacheTag;
    $("#npQuality").style.display = "inline-block";
  }

  // ── playback ─────────────────────────────────────────────────
  async function loadAndPlay(t) {
    ignoreEndUntil = Date.now() + 8000;
    advancing = true;
    userPaused = false;
    curTrack = Object.assign({}, t);
    $("#npTitle").textContent = t.name;
    $("#npArtist").textContent = t.artist || "未知艺术家";
    $("#npQuality").style.display = "none";
    setCacheBadge(!!t.cached);
    showNpCover(t.cover || "");
    lyricData = [];
    lyricCur = -1;
    if (viewMode === "lyrics") updateRightPanel();

    // pass duration + selected quality (chksz jymaster etc.)
    var extra = {};
    if (t.duration) extra.duration = t.duration;
    var songQs = "?" + levelQuery(extra);
    var sr = await api("/api/song/" + t.id + songQs);
    // 有 stream 即可播：url 可能是 meting 代理地址；404/无 data 才算失败
    if (sr && sr.ok && sr.data) {
      curTrack = Object.assign(curTrack, sr.data);
      curTrack.requestedLevel = audioLevel;
      if (sr.data.name) $("#npTitle").textContent = sr.data.name;
      if (sr.data.artist) $("#npArtist").textContent = sr.data.artist;
      if (sr.data.cover) showNpCover(sr.data.cover);
      setCacheBadge(!!sr.data.cached);

      audio.src =
        "/api/stream/" + t.id + "?" + levelQuery({ t: Date.now() });
      audio.loop = mode === "repeat-one";
      loopFail = 0;
      loopGaveUp = false;
      loopRecoverAt = 0;
      skipFails = 0;
      try {
        await audio.play();
      } catch (e) {
        toast("点击播放按钮开始");
      }

      renderQuality(sr.data);
      showActionButtons(true);
      // 同步元数据到自选/电台两份列表里同 id 的项
      [playlist, radioPlaylist].forEach(function (list) {
        list.forEach(function (p, i) {
          if (String(p.id) === String(t.id)) {
            list[i].cached = !!sr.data.cached;
            if (sr.data.name) list[i].name = sr.data.name;
            if (sr.data.artist) list[i].artist = sr.data.artist;
            if (sr.data.cover) list[i].cover = sr.data.cover;
            if (sr.data.duration) list[i].duration = sr.data.duration;
          }
        });
      });
    } else {
      toast("❌ 获取播放链接失败");
      showActionButtons(false);
      skipToNextOnFail();
    }

    var lr = await api("/api/lyric/" + t.id);
    if (lr && lr.ok && lr.data) {
      parseLyric(lr.data.lrc || lr.data.lyric || "", lr.data.tlrc || lr.data.tlyric || "");
    }

    scheduleSave();
    updateCardStates();
    if (
      viewMode === "playlist" ||
      viewMode === "radio" ||
      viewMode === "lyrics"
    ) {
      updateRightPanel();
    }
  }

  function showNpCover(url) {
    var wrap = $("#npCoverWrap");
    var img = $("#npCover");
    if (!url) {
      wrap.classList.remove("has-cover");
      img.src = "";
      return;
    }
    img.src = coverSrc(url);
    img.onload = function () {
      wrap.classList.add("has-cover");
    };
    img.onerror = function () {
      wrap.classList.remove("has-cover");
      img.src = "";
    };
  }

  function parseLyric(lrc, tlrc) {
    lyricData = [];
    var om = new Map();
    var tm = new Map();
    var re = /\[(\d+):(\d+(?:\.\d+)?)\](.*)/g;
    var matches;
    if (lrc) {
      while ((matches = re.exec(lrc)) !== null) {
        var ms =
          parseInt(matches[1], 10) * 60000 +
          Math.round(parseFloat(matches[2]) * 1000);
        var tx = matches[3].trim();
        if (tx) om.set(ms, tx);
      }
    }
    if (tlrc) {
      re.lastIndex = 0;
      while ((matches = re.exec(tlrc)) !== null) {
        var ms2 =
          parseInt(matches[1], 10) * 60000 +
          Math.round(parseFloat(matches[2]) * 1000);
        var tx2 = matches[3].trim();
        if (tx2) tm.set(ms2, tx2);
      }
    }
    var all = new Set();
    om.forEach(function (_v, k) {
      all.add(k);
    });
    tm.forEach(function (_v, k) {
      all.add(k);
    });
    lyricData = Array.from(all)
      .sort(function (a, b) {
        return a - b;
      })
      .map(function (ms) {
        return { ms: ms, orig: om.get(ms) || "", tran: tm.get(ms) || "" };
      });
    if (viewMode === "lyrics") updateRightPanel();
  }

  function syncLyric() {
    if (!lyricData.length) return;
    var pos = audio.currentTime * 1000;
    var idx = -1;
    for (var i = 0; i < lyricData.length; i++) {
      if (lyricData[i].ms <= pos) idx = i;
      else break;
    }
    if (idx === lyricCur) return;
    lyricCur = idx;
    if (viewMode !== "lyrics") return;
    $$(".lyric-line").forEach(function (el, i) {
      el.classList.toggle("active", i === idx);
      if (i === idx) el.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }
  setInterval(syncLyric, 200);

  // ── download / copy ──────────────────────────────────────────
  function downloadSong(track) {
    var displayName = (track.artist ? track.artist + " - " : "") + track.name;
    var lvLabel = LEVEL_LABELS[audioLevel] || audioLevel;
    toast("下载 [" + lvLabel + "]: " + displayName);
    var a = document.createElement("a");
    a.href = "/api/download/" + track.id + "?" + levelQuery();
    // extension resolved by server Content-Disposition (flac/mp3)
    var safe = displayName.replace(/[<>:"/\\|?*]/g, "_");
    var extHint =
      audioLevel === "lossless" ||
      audioLevel === "hires" ||
      audioLevel === "jymaster"
        ? ".flac"
        : ".mp3";
    a.download = safe + extHint;
    a.click();
  }

  function downloadLyric() {
    if (!curTrack) return;
    var a = document.createElement("a");
    a.href = "/api/download-lyric/" + curTrack.id;
    a.click();
    toast("下载歌词");
  }

  function copyText(text, okMsg) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () {
          toast(okMsg || "已复制");
        },
        function () {
          fallbackCopy(text, okMsg);
        }
      );
    } else {
      fallbackCopy(text, okMsg);
    }
  }

  function fallbackCopy(text, okMsg) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      toast(okMsg || "已复制");
    } catch (e) {
      prompt("请手动复制：", text);
    }
    document.body.removeChild(ta);
  }

  function copyStreamUrl() {
    if (!isAdmin) {
      toast("需要管理员权限");
      return;
    }
    if (!curTrack) return;
    var url =
      curTrack.streamUrl ||
      publicBase +
        "/api/stream/" +
        curTrack.id +
        "?" +
        levelQuery();
    copyText(url, "✅ 已复制播放地址 (" + (LEVEL_LABELS[audioLevel] || audioLevel) + ")");
  }

  function copyShare() {
    if (!curTrack) return;
    var url = curTrack.pageUrl || publicBase + "/?play=" + curTrack.id;
    copyText(url, "✅ 已复制分享链接");
  }

  // ── search ───────────────────────────────────────────────────
  async function doSearch() {
    var kw = $("#searchInput").value.trim();
    if (!kw) return;
    // If looks like id/link, use parse path for better meta
    if (/^\d{5,}$/.test(kw) || /163\.com|music\.163|song\?id=/i.test(kw)) {
      var t = await resolveAndFill(kw, { play: false, staySearch: true });
      if (t) return;
    }
    setActiveTab("search");
    $("#rpBody").innerHTML =
      '<div class="empty"><div class="icon">⏳</div>搜索中...</div>';
    var r = await api("/api/search?q=" + encodeURIComponent(kw));
    searchCache = r.ok && r.data ? r.data : [];
    updateRightPanel();
    if (!searchCache.length) toast("没有结果");
  }

  async function loadNeteasePlaylist() {
    var raw = prompt("网易云歌单 ID 或链接：");
    if (!raw) return;
    var pid = raw;
    var m = raw.match(/[?&]id=(\d+)/);
    if (m) pid = m[1];
    if (!/^\d+$/.test(pid)) {
      toast("无效 ID");
      return;
    }
    toast("加载中…");
    var r = await api("/api/playlist/" + pid);
    if (r.ok && r.data && r.data.length) {
      playlist = r.data;
      playSource = "my";
      curIdx = -1;
      scheduleSave();
      setActiveTab("playlist");
      updateRightPanel();
      toast("已加载 " + r.data.length + " 首到「我的列表」");
    } else {
      toast("加载失败");
    }
  }

  /** 今日电台：每次重新随机一批；exclude 上一批 id，尽量不重复 */
  async function loadTodayRadio() {
    var btn = $("#btnRadio");
    var btn2 = $("#btnReloadRadio");
    if (btn) btn.disabled = true;
    if (btn2) btn2.disabled = true;
    toast("正在换一批歌…");
    try {
      var exclude = radioPlaylist
        .map(function (t) {
          return t.id;
        })
        .filter(Boolean)
        .join(",");
      var url =
        "/api/radio/today?limit=30&_=" +
        Date.now() +
        (exclude ? "&exclude=" + encodeURIComponent(exclude) : "");
      var r = await api(url);
      if (!r.ok || !r.data || !r.data.length) {
        toast("❌ 今日电台加载失败");
        return;
      }
      radioPlaylist = r.data.map(function (t) {
        return {
          id: t.id,
          name: t.name || String(t.id),
          artist: t.artist || "",
          album: t.album || "",
          cover: t.cover || "",
          duration: t.duration || 0,
          cached: !!t.cached,
        };
      });
      radioDate = r.date || "";
      playSource = "radio";
      curIdx = 0;
      mode = "loop";
      applyModeUI();
      scheduleSave();
      setActiveTab("radio");
      updateRightPanel();
      var tip =
        typeof r.overlap === "number" && r.overlap > 0
          ? "（重复 " + r.overlap + " 首）"
          : "（已尽量换新）";
      toast(
        "📻 新一批 " + radioPlaylist.length + " 首 " + tip
      );
      await loadAndPlay(radioPlaylist[0]);
    } catch (e) {
      console.warn(e);
      toast("❌ 今日电台出错");
    } finally {
      if (btn) btn.disabled = false;
      if (btn2) btn2.disabled = false;
    }
  }

  // ── events ───────────────────────────────────────────────────
  try {
  $("#tabSearch").onclick = function () {
    setActiveTab("search");
    updateRightPanel();
  };
  $("#tabPlaylist").onclick = function () {
    setActiveTab("playlist");
    updateRightPanel();
  };
  var tabRadio = $("#tabRadio");
  if (tabRadio) {
    tabRadio.onclick = function () {
      setActiveTab("radio");
      updateRightPanel();
    };
  }
  $("#tabLibrary").onclick = async function () {
    if (!isAdmin) {
      toast("需要管理员权限");
      return;
    }
    setActiveTab("library");
    await loadLibrary();
    updateRightPanel();
  };
  $("#tabLyrics").onclick = function () {
    setActiveTab("lyrics");
    updateRightPanel();
  };
  $("#searchBtn").onclick = doSearch;
  $("#searchInput").onkeydown = function (e) {
    if (e.key === "Enter") doSearch();
  };
  $("#btnResolve").onclick = promptResolve;
  $("#btnLoadPl").onclick = loadNeteasePlaylist;
  var btnRadio = $("#btnRadio");
  if (btnRadio) btnRadio.onclick = loadTodayRadio;
  var btnReloadRadio = $("#btnReloadRadio");
  if (btnReloadRadio) btnReloadRadio.onclick = loadTodayRadio;
  var btnClearRadio = $("#btnClearRadio");
  if (btnClearRadio) btnClearRadio.onclick = clearRadioPlaylist;
  $("#btnClearPl").onclick = clearPlaylist;
  $("#btnExportPl").onclick = function (e) {
    e.stopPropagation();
    toggleExportMenu();
  };
  var exportMenu = $("#exportMenu");
  if (exportMenu) {
    exportMenu.onclick = function (e) {
      var t = e.target;
      var fmt = t && t.getAttribute ? t.getAttribute("data-export") : "";
      if (fmt) exportCurrentList(fmt);
    };
  }
  document.addEventListener("click", function (e) {
    var menu = $("#exportMenu");
    if (!menu || menu.hidden) return;
    var wrap = $("#exportWrap");
    if (wrap && wrap.contains(e.target)) return;
    closeExportMenu();
  });
  $("#btnImportPl").onclick = function () {
    $("#importFile").click();
  };
  $("#btnRefreshLib").onclick = async function () {
    if (!isAdmin) return;
    await loadLibrary();
    if (viewMode !== "library") setActiveTab("library");
    updateRightPanel();
    toast("曲库已刷新");
  };
  $("#importFile").onchange = function () {
    if (this.files && this.files[0]) importPlaylist(this.files[0]);
    this.value = "";
  };

  var btnTheme = $("#btnTheme");
  if (btnTheme) btnTheme.onclick = toggleTheme;

  // Logo ×5 → admin unlock
  $("#logoBtn").onclick = function (e) {
    e.preventDefault();
    if (isAdmin) {
      // already admin: single click does nothing special
      return false;
    }
    logoClicks += 1;
    clearTimeout(logoClickTimer);
    logoClickTimer = setTimeout(function () {
      logoClicks = 0;
    }, 2500);
    if (logoClicks >= 5) {
      logoClicks = 0;
      clearTimeout(logoClickTimer);
      tryAdminUnlock();
    }
    return false;
  };
  var exitBtn = $("#btnAdminExit");
  if (exitBtn) exitBtn.onclick = exitAdmin;

  // ── Admin upstream modal (probe + switch) ───────────────────
  var upState = {
    current: "meting",
    providers: [],
    probe: null, // last probe payload
  };

  function closeUpstreamModal() {
    var m = $("#upstreamModal");
    if (m) m.hidden = true;
  }

  function openUpstreamModal() {
    var m = $("#upstreamModal");
    if (m) m.hidden = false;
  }

  function escHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderUpstreamList() {
    var box = $("#upList");
    if (!box) return;
    var cur = upState.current || "meting";
    var probeMap = {};
    if (upState.probe && upState.probe.results) {
      upState.probe.results.forEach(function (r) {
        probeMap[r.id] = r;
      });
    }
    var autoInfo = (upState.probe && upState.probe.auto) || null;

    var cards = (upState.providers || []).map(function (p) {
      var isCur = p.id === cur;
      var pr = p.id === "auto" ? autoInfo : probeMap[p.id];
      var hasProbe = !!pr;
      var alive = hasProbe ? !!pr.alive : null;
      var cls = "up-card" + (isCur ? " is-current" : "") + (alive === false ? " is-dead" : "");
      var badges = "";
      if (isCur) badges += '<span class="up-badge current">当前</span>';
      if (alive === true) badges += '<span class="up-badge alive">可达</span>';
      if (alive === false) badges += '<span class="up-badge dead">失败</span>';
      if (!hasProbe && p.id !== "auto")
        badges += '<span class="up-badge">未测</span>';
      if (p.kind && p.kind !== "auto")
        badges +=
          '<span class="up-kind-tag">' + escHtml(p.kind) + "</span>";
      if (p.builtin) badges += '<span class="up-badge">内置</span>';

      var meta = "";
      if (hasProbe && p.id !== "auto") {
        meta =
          '<div class="up-meta">' +
          (alive ? "✅ " + escHtml(pr.summary || "可用") : "❌ " + escHtml(pr.summary || "不可用")) +
          (pr.ms != null ? ' · <span class="ms">' + pr.ms + " ms</span>" : "") +
          " · 通过 " +
          (pr.ok_count || 0) +
          "/" +
          (pr.total || 0) +
          "</div>";
      } else if (p.id === "auto" && autoInfo) {
        meta =
          '<div class="up-meta">' +
          escHtml(autoInfo.summary || "") +
          "</div>";
      }

      var checksHtml = "";
      if (pr && pr.checks && pr.checks.length) {
        checksHtml =
          '<div class="up-checks">' +
          pr.checks
            .map(function (c) {
              var ok = !!c.ok;
              var tip = ok
                ? c.detail || c.ms + "ms"
                : c.error || "fail";
              return (
                '<span class="up-check ' +
                (ok ? "ok" : "fail") +
                '" title="' +
                escHtml(tip) +
                '">' +
                (ok ? "✓ " : "✗ ") +
                escHtml(c.label || c.name) +
                " " +
                (c.ms != null ? c.ms + "ms" : "") +
                (ok
                  ? ""
                  : '<span class="err">' + escHtml((c.error || "").slice(0, 40)) + "</span>") +
                "</span>"
              );
            })
            .join("") +
          "</div>";
      }

      var ep = p.endpoint
        ? '<div class="up-card-desc">' + escHtml(p.desc || "") + "<br><code>" + escHtml(p.endpoint) + "</code></div>"
        : '<div class="up-card-desc">' + escHtml(p.desc || "") + "</div>";

      var useLabel = isCur ? "✓ 使用中" : "选用此源";
      var delBtn = p.deletable
        ? '<button type="button" class="up-btn danger" data-del="' +
          escHtml(p.id) +
          '">删除</button>'
        : "";
      return (
        '<div class="' +
        cls +
        '" data-id="' +
        escHtml(p.id) +
        '">' +
        '<div class="up-card-top">' +
        '<div class="up-card-title">' +
        escHtml(p.name) +
        " " +
        badges +
        "</div>" +
        "</div>" +
        ep +
        meta +
        checksHtml +
        '<div class="up-card-actions">' +
        '<button type="button" class="up-btn use' +
        (isCur ? " is-current" : "") +
        '" data-use="' +
        escHtml(p.id) +
        '"' +
        (isCur ? " disabled" : "") +
        ">" +
        useLabel +
        "</button>" +
        delBtn +
        "</div></div>"
      );
    });

    box.innerHTML = cards.join("") || '<div class="empty">加载中…</div>';

    box.querySelectorAll("[data-use]").forEach(function (btn) {
      btn.onclick = function () {
        setUpstreamProvider(btn.getAttribute("data-use"));
      };
    });
    box.querySelectorAll("[data-del]").forEach(function (btn) {
      btn.onclick = function () {
        deleteUpstreamSource(btn.getAttribute("data-del"));
      };
    });

    var rec = $("#upRecommend");
    if (rec) {
      if (upState.probe && upState.probe.recommend) {
        rec.textContent =
          "建议: " +
          upState.probe.recommend +
          (upState.probe.probe_ms
            ? "（整轮测速 " + upState.probe.probe_ms + " ms）"
            : "");
      } else {
        rec.textContent = "点「测速 / 测活」可检查 VPS 到各上游的连通性与延迟。";
      }
    }
  }

  async function loadUpstreamStatus() {
    var resp = await fetch("/api/admin/upstream", { headers: adminHeaders() });
    var j = await resp.json();
    if (!resp.ok || !j.ok) throw new Error(j.error || "load failed");
    upState.current = j.current || "meting";
    upState.providers = j.providers || [];
    renderUpstreamList();
  }

  async function runUpstreamProbe() {
    var status = $("#upProbeStatus");
    var btn = $("#upBtnProbe");
    if (btn) btn.disabled = true;
    if (status) status.textContent = "测速中（可能需要 10–20 秒）…";
    try {
      var resp = await fetch("/api/admin/upstream/probe", {
        method: "POST",
        headers: adminHeaders(),
        body: JSON.stringify({ sid: "416892104", keyword: "晴天" }),
      });
      var j = await resp.json();
      if (!resp.ok || !j.ok) {
        if (status) status.textContent = "测速失败: " + (j.error || resp.status);
        toast("❌ 测速失败");
        return;
      }
      upState.probe = j;
      if (j.current) upState.current = j.current;
      renderUpstreamList();
      var aliveN = (j.results || []).filter(function (r) {
        return r.alive;
      }).length;
      if (status)
        status.textContent =
          "完成 · " +
          aliveN +
          "/" +
          (j.results || []).length +
          " 源可达 · 总耗时 " +
          (j.probe_ms || "?") +
          " ms";
      toast("⚡ 测速完成");
    } catch (e) {
      console.warn(e);
      if (status) status.textContent = "测速异常";
      toast("❌ 测速异常");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function setUpstreamProvider(provider) {
    if (!provider) return;
    try {
      var r2 = await fetch("/api/admin/upstream", {
        method: "POST",
        headers: adminHeaders(),
        body: JSON.stringify({ provider: provider }),
      });
      var j2 = await r2.json();
      if (!r2.ok || !j2.ok) {
        toast("切换失败: " + (j2.error || r2.status));
        return;
      }
      upState.current = j2.current || provider;
      if (j2.providers) upState.providers = j2.providers;
      renderUpstreamList();
      toast("✅ 上游已切换为 " + upState.current + "（立即生效）");
    } catch (e) {
      console.warn(e);
      toast("上游设置出错");
    }
  }

  async function deleteUpstreamSource(sid) {
    if (!sid) return;
    if (!confirm("确定删除源「" + sid + "」？内置源不可删。")) return;
    try {
      var r = await fetch(
        "/api/admin/upstream/sources/" + encodeURIComponent(sid),
        { method: "DELETE", headers: adminHeaders() }
      );
      var j = await r.json();
      if (!r.ok || !j.ok) {
        toast("删除失败: " + (j.error || r.status));
        return;
      }
      upState.current = j.current || upState.current;
      upState.providers = j.providers || [];
      if (upState.probe && upState.probe.results) {
        upState.probe.results = upState.probe.results.filter(function (x) {
          return x.id !== sid;
        });
      }
      renderUpstreamList();
      toast("已删除 " + sid);
    } catch (e) {
      console.warn(e);
      toast("删除异常");
    }
  }

  async function addUpstreamSource() {
    var name = ($("#upAddName") && $("#upAddName").value) || "";
    var kind = ($("#upAddKind") && $("#upAddKind").value) || "meting";
    var endpoint = ($("#upAddEndpoint") && $("#upAddEndpoint").value) || "";
    var desc = ($("#upAddDesc") && $("#upAddDesc").value) || "";
    if (!endpoint.trim()) {
      toast("请填写 Endpoint URL");
      return;
    }
    try {
      var r = await fetch("/api/admin/upstream/sources", {
        method: "POST",
        headers: adminHeaders(),
        body: JSON.stringify({
          name: name.trim(),
          kind: kind,
          endpoint: endpoint.trim(),
          desc: desc.trim(),
        }),
      });
      var j = await r.json();
      if (!r.ok || !j.ok) {
        toast("添加失败: " + (j.error || r.status));
        return;
      }
      upState.providers = j.providers || [];
      if (j.current) upState.current = j.current;
      renderUpstreamList();
      var form = $("#upAddForm");
      if (form) form.hidden = true;
      if ($("#upAddEndpoint")) $("#upAddEndpoint").value = "";
      if ($("#upAddName")) $("#upAddName").value = "";
      if ($("#upAddDesc")) $("#upAddDesc").value = "";
      toast("✅ 已添加 " + ((j.added && j.added.id) || "源"));
    } catch (e) {
      console.warn(e);
      toast("添加异常");
    }
  }

  async function openUpstreamPicker() {
    if (!isAdmin || !adminToken) {
      toast("需要管理员权限");
      return;
    }
    openUpstreamModal();
    var status = $("#upProbeStatus");
    var form = $("#upAddForm");
    if (form) form.hidden = true;
    try {
      await loadUpstreamStatus();
      refreshChkszQuota();
      if (status && !upState.probe) status.textContent = "尚未测速";
    } catch (e) {
      console.warn(e);
      toast("无法读取上游配置");
      closeUpstreamModal();
    }
  }

  var btnUpstream = $("#btnUpstream");
  if (btnUpstream) btnUpstream.onclick = openUpstreamPicker;
  var quotaChip = $("#chkszQuota");
  if (quotaChip) {
    quotaChip.onclick = function () {
      refreshChkszQuota();
    };
  }
  var upQuotaRefresh = $("#upQuotaRefresh");
  if (upQuotaRefresh) {
    upQuotaRefresh.onclick = function () {
      refreshChkszQuota();
    };
  }
  var upClose = $("#upModalClose");
  if (upClose) upClose.onclick = closeUpstreamModal;
  var upMask = $("#upstreamModal");
  if (upMask) {
    upMask.addEventListener("click", function (e) {
      if (e.target === upMask) closeUpstreamModal();
    });
  }
  var upBtnProbe = $("#upBtnProbe");
  if (upBtnProbe) upBtnProbe.onclick = runUpstreamProbe;
  var upBtnShowAdd = $("#upBtnShowAdd");
  if (upBtnShowAdd) {
    upBtnShowAdd.onclick = function () {
      var form = $("#upAddForm");
      if (form) form.hidden = !form.hidden;
    };
  }
  var upBtnAdd = $("#upBtnAdd");
  if (upBtnAdd) upBtnAdd.onclick = addUpstreamSource;
  var upBtnAddCancel = $("#upBtnAddCancel");
  if (upBtnAddCancel) {
    upBtnAddCancel.onclick = function () {
      var form = $("#upAddForm");
      if (form) form.hidden = true;
    };
  }
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      var menu = $("#exportMenu");
      if (menu && !menu.hidden) {
        closeExportMenu();
        return;
      }
      var m = $("#upstreamModal");
      if (m && !m.hidden) closeUpstreamModal();
    }
  });

  $("#btnDlSong").onclick = function () {
    if (curTrack) downloadSong(curTrack);
  };
  $("#btnDlLyric").onclick = downloadLyric;
  $("#btnCopyUrl").onclick = copyStreamUrl;
  $("#btnShare").onclick = copyShare;
  $("#btnCacheSong").onclick = function () {
    if (curTrack) cacheTrack(curTrack);
  };

  $("#btnPlay").onclick = function () {
    if (audio.paused) {
      var q = activePlaylist();
      if (nearMediaEnd() && mode !== "repeat-one" && q.length > 1) {
        playNextInQueue({ force: true });
        return;
      }
      if (!audio.getAttribute("src") && q.length) {
        curIdx = Math.max(0, curIdx);
        if (curIdx >= q.length) curIdx = 0;
        loadAndPlay(q[curIdx]);
      } else {
        audio.play().catch(function () {
          if (q.length) loadAndPlay(q[Math.max(0, curIdx)]);
        });
      }
    } else {
      userPaused = true;
      audio.pause();
    }
  };

  $("#btnNext").onclick = function () {
    var q = activePlaylist();
    if (!q.length) return;
    if (mode === "shuffle") {
      curIdx = Math.floor(Math.random() * q.length);
    } else if (mode === "repeat-one") {
      audio.currentTime = 0;
      audio.play();
      return;
    } else {
      curIdx = (curIdx + 1) % q.length;
    }
    loadAndPlay(q[curIdx]);
  };

  $("#btnPrev").onclick = function () {
    var q = activePlaylist();
    if (!q.length) return;
    curIdx = (curIdx - 1 + q.length) % q.length;
    loadAndPlay(q[curIdx]);
  };

  $("#btnMode").onclick = function () {
    var ks = ["loop", "shuffle", "repeat-one"];
    mode = ks[(ks.indexOf(mode) + 1) % 3];
    applyModeUI();
    scheduleSave();
  };

  $("#btnVol").onclick = function () {
    audio.muted = !audio.muted;
    syncVolUI();
    scheduleSave();
  };

  var volDown = $("#btnVolDown");
  var volUp = $("#btnVolUp");
  if (volDown) {
    volDown.onclick = function () {
      bumpVolume(-0.1);
    };
  }
  if (volUp) {
    volUp.onclick = function () {
      bumpVolume(0.1);
    };
  }

  function onVolSliderInput() {
    var v = parseInt($("#volSlider").value, 10) / 100;
    if (isIOSDevice()) {
      // keep slider visual in sync but don't pretend it works
      syncVolUI();
      var tip = $("#volIosTip");
      if (tip) tip.hidden = false;
      return;
    }
    setVolume(v, { silent: true });
  }
  $("#volSlider").addEventListener("input", onVolSliderInput);
  $("#volSlider").addEventListener("change", onVolSliderInput);
  // Better mobile dragging
  $("#volSlider").addEventListener(
    "touchstart",
    function () {
      /* allow native range behavior */
    },
    { passive: true }
  );

  $("#progTrack").onclick = function (e) {
    var r = $("#progTrack").getBoundingClientRect();
    if (audio.duration && audio.duration > 0) {
      audio.currentTime = ((e.clientX - r.left) / r.width) * audio.duration;
    }
  };

  audio.ontimeupdate = function () {
    if (audio.duration) {
      $("#progFill").style.width =
        (audio.currentTime / audio.duration) * 100 + "%";
      $("#timeCur").textContent = fmt(audio.currentTime);
    }
  };
  audio.ondurationchange = function () {
    $("#timeTot").textContent = fmt(audio.duration);
    // Once real duration is known, refine meting kbps from size if needed
    if (curTrack && audio.duration > 0) {
      var durMs = Math.round(audio.duration * 1000);
      curTrack.duration = durMs;
      if (curTrack.size && (!curTrack.br || curTrack.source === "meting")) {
        var est = Math.round((Number(curTrack.size) * 8) / audio.duration);
        if (est > 0) {
          curTrack.br = est;
          if (est >= 900000) curTrack.level = "lossless";
          else if (est >= 300000) curTrack.level = "exhigh";
          else if (est >= 180000) curTrack.level = "higher";
          else if (est >= 100000) curTrack.level = "standard";
          renderQuality(curTrack);
        }
      }
    }
  };
  var loopFail = 0;
  var loopGaveUp = false;
  var loopRecoverAt = 0;
  var advancing = false;
  var advanceAt = 0;
  var skipFails = 0;
  var userPaused = false;
  var ignoreEndUntil = 0;

  function isSingleLoop() {
    return mode === "repeat-one";
  }

  function nearMediaEnd() {
    var d = audio.duration;
    var t = audio.currentTime;
    if (!d || !isFinite(d) || d <= 0 || !isFinite(t)) return false;
    if (audio.ended) return true;
    if (t >= d - 1.5) return true;
    return Math.floor(t) >= Math.floor(d) && t > 1;
  }

  function remountCurrentStream() {
    if (!curTrack) return Promise.reject();
    audio.src =
      "/api/stream/" + curTrack.id + "?" + levelQuery({ t: Date.now() });
    if (mode === "repeat-one") audio.loop = true;
    return audio.play();
  }

  function recoverSingleLoop() {
    if (mode !== "repeat-one" || !curTrack || loopGaveUp) return;
    var now = Date.now();
    if (now - loopRecoverAt < 2500) return;
    if (loopFail >= 2) {
      loopGaveUp = true;
      toast("⚠ 播放失败");
      return;
    }
    loopRecoverAt = now;
    loopFail += 1;
    remountCurrentStream().catch(function () {});
  }

  function skipToNextOnFail() {
    var q = activePlaylist();
    if (mode === "repeat-one" || q.length < 2) return;
    skipFails += 1;
    if (skipFails >= Math.min(8, q.length)) {
      toast("⚠ 连续失败，已停止");
      skipFails = 0;
      return;
    }
    toast("跳过无法播放的歌曲");
    setTimeout(function () {
      playNextInQueue({ force: true });
    }, 400);
  }

  function playNextInQueue(opts) {
    opts = opts || {};
    var now = Date.now();
    if (!opts.force && (advancing || now < ignoreEndUntil) && now - advanceAt < 2500) {
      return;
    }
    advancing = true;
    advanceAt = now;
    ignoreEndUntil = now + 8000;
    userPaused = false;
    var q = activePlaylist();
    if (mode === "repeat-one") {
      remountCurrentStream().catch(function () {
        recoverSingleLoop();
      });
      return;
    }
    if (!q.length) {
      if (curTrack) {
        remountCurrentStream().catch(function () {});
      } else {
        advancing = false;
      }
      return;
    }
    if (q.length === 1) {
      loadAndPlay(q[0]);
      return;
    }
    if (mode === "shuffle") {
      var n = curIdx;
      var guard = 0;
      if (q.length > 1) {
        while (n === curIdx && guard++ < 12) {
          n = Math.floor(Math.random() * q.length);
        }
      }
      curIdx = n;
    } else {
      if (curIdx < 0 || curIdx >= q.length) curIdx = 0;
      else curIdx = (curIdx + 1) % q.length;
    }
    loadAndPlay(q[curIdx]);
  }

  audio.onended = function () {
    playNextInQueue();
  };
  audio.onplay = function () {
    userPaused = false;
    advancing = false;
    ignoreEndUntil = 0;
    $("#btnPlay").textContent = "⏸";
    updateCardStates();
  };
  audio.ontimeupdate = (function (prev) {
    return function () {
      if (prev) prev.apply(this, arguments);
      if (audio.currentTime > 1.5 && audio.currentTime < (audio.duration || 9e9) - 2) {
        loopFail = 0;
        loopGaveUp = false;
        skipFails = 0;
      }
    };
  })(audio.ontimeupdate);
  audio.onpause = function () {
    $("#btnPlay").textContent = "▶";
    if (userPaused) return;
    if (Date.now() < ignoreEndUntil) return;
    if (nearMediaEnd() || audio.ended) {
      setTimeout(function () {
        if (userPaused || !audio.paused) return;
        if (Date.now() < ignoreEndUntil) return;
        if (nearMediaEnd() || audio.ended) playNextInQueue();
      }, 200);
    }
  };
  audio.onstalled = function () {
    if (userPaused || Date.now() < ignoreEndUntil) return;
    if (nearMediaEnd()) playNextInQueue();
  };
  audio.onerror = function () {
    if (mode === "repeat-one") {
      recoverSingleLoop();
      return;
    }
    if (nearMediaEnd() || audio.ended) {
      playNextInQueue();
      return;
    }
    toast("⚠ 播放失败，尝试下一首");
    skipToNextOnFail();
  };

  setInterval(function () {
    if (!curTrack || !audio.getAttribute("src")) return;
    if (userPaused) return;
    if (Date.now() < ignoreEndUntil) return;
    if (!audio.paused && !audio.ended) return;
    if (!(nearMediaEnd() || audio.ended)) return;
    if (mode === "repeat-one") {
      recoverSingleLoop();
      return;
    }
    playNextInQueue();
  }, 700);

  document.onkeydown = function (e) {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
    if (e.altKey && (e.code === "ArrowUp" || e.code === "ArrowDown")) {
      if (e.repeat) return;
      if (viewMode === "playlist" || viewMode === "radio") {
        e.preventDefault();
        var src = viewMode === "radio" ? "radio" : "my";
        var row =
          e.target && e.target.closest
            ? e.target.closest(".pl-item")
            : null;
        var idx = row
          ? parseInt(row.dataset.idx, 10)
          : playSource === src
            ? curIdx
            : -1;
        if (idx >= 0) {
          moveTrack(src, idx, idx + (e.code === "ArrowUp" ? -1 : 1));
        }
      }
      return;
    }
    switch (e.code) {
      case "Space":
        e.preventDefault();
        $("#btnPlay").click();
        break;
      case "ArrowRight":
        $("#btnNext").click();
        break;
      case "ArrowLeft":
        $("#btnPrev").click();
        break;
      case "ArrowUp":
        bumpVolume(0.05);
        break;
      case "ArrowDown":
        bumpVolume(-0.05);
        break;
      case "KeyM":
        $("#btnVol").click();
        break;
      case "Delete":
        if (viewMode === "playlist" && playSource === "my" && curIdx >= 0) {
          removeFromList("my", curIdx);
        } else if (
          viewMode === "radio" &&
          playSource === "radio" &&
          curIdx >= 0
        ) {
          removeFromList("radio", curIdx);
        }
        break;
    }
  };

  // ── audio level select ───────────────────────────────────────
  loadAudioLevel();
  var levelSel = $("#audioLevel");
  if (levelSel) {
    levelSel.value = audioLevel;
    levelSel.addEventListener("change", function () {
      setAudioLevel(levelSel.value, { reload: true });
    });
  }

  // ── boot ─────────────────────────────────────────────────────
  if (isIOSDevice()) {
    document.body.classList.add("is-ios");
    var iosTip = $("#volIosTip");
    if (iosTip) iosTip.hidden = false;
  }
  loadAdminSession();
  loadState();
  applyModeUI();
  if (typeof audio.volume !== "number" || isNaN(audio.volume)) audio.volume = 0.7;
  // Ensure non-iOS starts at saved/default volume
  if (!isIOSDevice() && (!audio.volume || audio.volume === 1) && !localStorage.getItem(STORAGE_KEY)) {
    audio.volume = 0.7;
  }
  syncVolUI();
  showActionButtons(false);

  if (playSource === "radio" && radioPlaylist.length) {
    setActiveTab("radio");
  } else if (playlist.length) {
    setActiveTab("playlist");
  }
  updateRightPanel();
  if (isAdmin) {
    loadLibrary();
    refreshChkszQuota();
  }
  setInterval(function () {
    if (isAdmin) refreshChkszQuota();
  }, 30000);

  var params = new URLSearchParams(location.search);
  var playId = params.get("play");
  var q = params.get("q");
  var parseQ = params.get("parse");
  if (q) {
    $("#searchInput").value = q;
    doSearch();
  }
  if (parseQ) {
    resolveAndFill(parseQ, { play: true });
  }
  if (playId) {
    var existing = playlist.find(function (x) {
      return String(x.id) === String(playId);
    });
    if (existing) {
      playSource = "my";
      curIdx = playlist.indexOf(existing);
      loadAndPlay(existing);
    } else {
      resolveAndFill(String(playId), { play: true });
    }
  }

  window.addEventListener("beforeunload", saveState);
  } catch (bootErr) {
    console.error("KAZAM boot failed", bootErr);
    try {
      setActiveTab("search");
      updateRightPanel();
    } catch (e2) {}
  }
})();
