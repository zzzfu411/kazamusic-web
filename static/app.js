/* KAZAM — immersive player front end.
   One <audio>. "我的列表" and "今日电台" live in localStorage (kazam.vps.v2,
   unchanged shape). A press on play sets the stream at once; metadata and
   lyrics arrive beside it, never in front of it. */
(function () {
  "use strict";

  // ── constants ────────────────────────────────────────────────────────
  var STORAGE_KEY = "kazam.vps.v2";
  var LEVEL_KEY = "kazam.audioLevel";
  var THEME_KEY = "kazam.theme";
  var COVER_ACCENT_KEY = "kazam.coverAccent";
  var ADMIN_TOKEN_KEY = "kazam.admin.token";
  var LEVEL_LABELS = {
    standard: "标准", higher: "较高", exhigh: "极高", lossless: "无损", hires: "Hi-Res", jymaster: "臻品母带",
  };
  var THEMES = {
    aurora: { name: "极光星云", meta: "#080c15" },
    apple: { name: "流光绯红", meta: "#09090c" },
    daylight: { name: "清透白昼", meta: "#eef2f9" },
    emerald: { name: "北欧森屿", meta: "#06100e" },
    sunset: { name: "落日微醺", meta: "#0e0816" },
  };
  var THEME_ORDER = ["aurora", "apple", "daylight", "emerald", "sunset"];
  var MODES = ["loop", "shuffle", "repeat-one"];
  var MODE_INFO = {
    loop: { icon: "#i-repeat", label: "列表循环" },
    shuffle: { icon: "#i-shuffle", label: "随机播放" },
    "repeat-one": { icon: "#i-repeat-one", label: "单曲循环" },
  };
  var DEFAULT_ACCENT = [56, 189, 248];
  var NETEASE = /^https?:\/\/p\d+\.music\.126\.net\//i;
  var LINKISH = /music\.163\.com|163cn\.tv|song\?id=|^\s*\d{5,}\s*$/i;
  var TRANSLATION_MARK = /^\s*\[(?:翻译歌词|中文翻译|翻译|译文|translation(?:\s+lyrics?)?)\]\s*$/i;

  var $ = function (s, root) { return (root || document).querySelector(s); };
  var $$ = function (s, root) { return Array.prototype.slice.call((root || document).querySelectorAll(s)); };
  var body = document.body;
  var root = document.documentElement;
  var audio = $("#audio");
  var publicBase = (window.KAZAM_PUBLIC_BASE || "").replace(/\/$/, "") || location.origin;
  var IOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var phoneQuery = window.matchMedia("(max-width: 860px)");

  var el = {
    search: $("#searchInput"), searchForm: $("#searchForm"), searchBadge: $("#searchBadge"),
    cover: $("#cover"), coverImg: $("#coverImg"), artA: $("#artA"), artB: $("#artB"),
    npTitle: $("#npTitle"), npArtist: $("#npArtist"), npQuality: $("#npQuality"), npCached: $("#npCached"), npActions: $("#npActions"),
    npLyricTicker: $("#npLyricTicker"), npLyricLine: $("#npLyricLine"),
    stageSourceLabel: $("#stageSourceLabel"), vinylModeLabel: $("#vinylModeLabel"),
    prog: $("#progTrack"), progFill: $("#progFill"), progBuf: $("#progBuf"), progTip: $("#progTip"), timeCur: $("#timeCur"), timeTot: $("#timeTot"),
    play: $("#btnPlay"), mode: $("#btnMode"), vol: $("#btnVol"), volSlider: $("#volSlider"), level: $("#audioLevel"),
    mini: $("#mini"), miniCover: $("#miniCover"), miniTitle: $("#miniTitle"), miniArtist: $("#miniArtist"), miniFill: $("#miniFill"), miniPlay: $("#miniPlay"),
    panelBody: $("#panelBody"), panelSub: $("#panelSub"), countMy: $("#countMy"), countRadio: $("#countRadio"), listFilter: $("#listFilter"),
    panelBatch: $("#panelBatch"), btnPlayAll: $("#btnPlayAll"), btnAddAll: $("#btnAddAll"),
    lyrics: $("#lyrics"), viewLyrics: $("#viewLyrics"), stageLyrics: $("#stageLyrics"),
    menu: $("#rowMenu"), toast: $("#toast"), exportMenu: $("#exportMenu"), exportBtn: $("#btnExportPl"),
    dlg: $("#dlg"), dlgTitle: $("#dlgTitle"), dlgDesc: $("#dlgDesc"), dlgInput: $("#dlgInput"), dlgActions: $("#dlgActions"),
    quota: $("#chkszQuota"), meta: $("#metaThemeColor"),
    btnTheme: $("#btnTheme"), themeMenu: $("#themeMenu"), themeList: $("#themeList"), btnCoverAccent: $("#btnCoverAccent"),
    btnVinyl: $("#btnVinylMode"), btnTheater: $("#btnTheater"), btnSleep: $("#btnSleep"), sleepBadge: $("#sleepBadge"), btnShortcuts: $("#btnShortcuts"),
  };
  var views = {
    search: $("#viewSearch"), playlist: $("#viewPlaylist"), radio: $("#viewRadio"), lyrics: $("#viewLyrics"), library: $("#viewLibrary"),
  };

  // ── state ────────────────────────────────────────────────────────────
  var S = { playlist: [], radio: [], radioDate: "", source: "my", idx: -1, mode: "loop", pos: 0 };
  var level = "exhigh";
  var curTheme = "aurora";
  var coverAccentOn = false;
  var cur = null;                 // the track on the stage (a copy, enriched by /api/song)
  var view = "search";
  var lastListView = "search";
  var search = { kw: "", state: "idle", results: [], error: "", token: 0 };
  var searchHist = [];
  var filterQuery = "";
  var library = { items: [], loaded: false, loading: false };
  var lyric = { lines: [], state: "idle", idx: -1, holdUntil: 0 };
  var admin = { on: false, token: "" };
  var sleepTimer = { endAt: 0, id: 0 };
  var playToken = 0;
  var userPaused = false;
  var failStreak = 0;
  var endedFor = -1;
  var prefetchedFor = -1;
  var bag = [], bagKey = "", shuffleHist = [];
  var radioLoading = false;
  var MOODS = [
    ["流行热歌", "华语流行 · 热门榜单", "#38bdf8"],
    ["独立民谣", "吉他叙事 · 温暖治愈", "#34d399"],
    ["日系动漫", "ACG 原声 · J-Pop", "#fb7185"],
    ["R&B 夜色", "微醺律动 · 深夜电台", "#a78bfa"],
    ["爵士咖啡馆", "慵懒萨克斯 · 钢琴三重奏", "#fb923c"],
    ["轻音乐", "专注学习 · 氛围纯音", "#818cf8"],
    ["粤语经典", "港乐黄金年代", "#f472b6"],
    ["电子律动", "Synth · House · 节拍", "#22d3ee"],
    ["经典摇滚", "乐队现场 · 失真吉他", "#ef4444"],
    ["影视金曲", "电影与剧集 OST", "#c084fc"],
  ];

  // ── helpers ──────────────────────────────────────────────────────────
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function enc(s) { return encodeURIComponent(String(s)); }
  function clamp(v, a, b) { return Math.min(b, Math.max(a, v)); }
  function fmt(sec) {
    if (!sec || !isFinite(sec) || sec < 0) return "0:00";
    sec = Math.floor(sec);
    return Math.floor(sec / 60) + ":" + String(sec % 60).padStart(2, "0");
  }
  function fmtMs(ms) { return ms ? fmt(Number(ms) / 1000) : ""; }
  function sameId(a, b) { return String(a) === String(b); }
  function cssEsc(s) { return window.CSS && CSS.escape ? CSS.escape(String(s)) : String(s).replace(/["\\]/g, "\\$&"); }
  function isPhone() { return phoneQuery.matches; }

  var toastTimer = 0;
  function toast(msg) {
    el.toast.textContent = msg;
    el.toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.toast.classList.remove("show"); }, 2400);
  }

  function api(path, opts) {
    opts = opts || {};
    var ctrl = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctrl) ctrl.abort(); }, opts.timeout || 20000);
    var init = { method: opts.method || "GET", headers: opts.headers || {} };
    if (opts.body !== undefined) {
      init.body = typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body);
      init.headers["Content-Type"] = "application/json";
    }
    if (ctrl) init.signal = ctrl.signal;
    return fetch(path, init)
      .then(function (r) {
        return r.json().catch(function () { return { ok: false }; }).then(function (j) {
          j = j && typeof j === "object" ? j : { ok: false };
          j._status = r.status;
          return j;
        });
      })
      .catch(function (e) { return { ok: false, error: e && e.name === "AbortError" ? "请求超时" : "网络错误", _status: 0 }; })
      .then(function (j) { clearTimeout(timer); return j; });
  }

  function useIcon(button, id) {
    var u = button && button.querySelector("use");
    if (u && u.getAttribute("href") !== id) u.setAttribute("href", id);
  }

  // ── covers ───────────────────────────────────────────────────────────
  // NetEase pictures are asked for at the size shown (param=NyN on their CDN);
  // a cached cover comes as the music server's small WebP copy; anything else,
  // or a NetEase picture that fails, goes through the same-origin proxy.
  function normCover(raw) {
    raw = String(raw || "").trim();
    [location.origin, publicBase].forEach(function (o) {
      if (o && raw.indexOf(o + "/api/") === 0) raw = raw.slice(o.length);
    });
    return raw;
  }
  function coverSrc(raw, size) {
    raw = normCover(raw);
    if (!raw) return "";
    if (raw.indexOf("/api/cache/") === 0) {
      return raw.split("?")[0] + "?size=" + (size <= 160 ? 160 : size <= 320 ? 320 : 480);
    }
    if (raw.indexOf("/api/cover-proxy") === 0) return /[?&]size=/.test(raw) ? raw : raw + "&size=" + size;
    if (raw.charAt(0) === "/") return raw;
    if (NETEASE.test(raw)) {
      var u = raw.replace(/^http:/i, "https:").replace(/([?&])param=\d+y\d+&?/, "$1").replace(/[?&]$/, "");
      return u + (u.indexOf("?") > -1 ? "&" : "?") + "param=" + size + "y" + size;
    }
    return "/api/cover-proxy?url=" + enc(raw) + "&size=" + size;
  }
  function coverFallback(raw, size) {
    raw = normCover(raw);
    return raw && NETEASE.test(raw) ? "/api/cover-proxy?url=" + enc(raw) + "&size=" + size : "";
  }
  function coverSameOrigin(raw, size) {
    raw = normCover(raw);
    if (!raw) return "";
    return raw.charAt(0) === "/" ? coverSrc(raw, size) : "/api/cover-proxy?url=" + enc(raw) + "&size=" + size;
  }
  function imgTag(raw, size, w) {
    var src = coverSrc(raw, size);
    if (!src) return "";
    var fb = coverFallback(raw, Math.max(size, 128));
    return '<img class="cimg" src="' + esc(src) + '"' + (fb ? ' data-fb="' + esc(fb) + '"' : "") +
      ' alt="" width="' + w + '" height="' + w + '" loading="lazy" decoding="async" referrerpolicy="no-referrer">';
  }
  // a cover that fails tries its proxy copy once, then leaves the note placeholder
  document.addEventListener("error", function (e) {
    var img = e.target;
    if (!img || img.tagName !== "IMG" || !img.classList.contains("cimg")) return;
    var fb = img.getAttribute("data-fb");
    if (fb && img.getAttribute("src") !== fb) { img.src = fb; return; }
    if (img.parentNode) img.parentNode.classList.add("broken");
  }, true);

  // ── accent colour from the cover ─────────────────────────────────────
  var accentCache = {};
  function rgbToHsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    var max = Math.max(r, g, b), min = Math.min(r, g, b), h = 0, s = 0, l = (max + min) / 2;
    if (max !== min) {
      var d = max - min;
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
      h /= 6;
    }
    return [h, s, l];
  }
  function hslToRgb(h, s, l) {
    function f(n) {
      var k = (n + h * 12) % 12, a = s * Math.min(l, 1 - l);
      return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))));
    }
    return [f(0), f(8), f(4)];
  }
  function seedAccent(str) {
    var s = String(str || "kazam-cover");
    var hash = 2166136261;
    for (var i = 0; i < s.length; i++) {
      hash ^= s.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    var h = ((hash >>> 0) % 360) / 360;
    return hslToRgb(h, 0.78, 0.64);
  }
  function pickAccent(img) {
    try {
      var c = document.createElement("canvas");
      c.width = c.height = 32;
      var ctx = c.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(img, 0, 0, 32, 32);
      var d = ctx.getImageData(0, 0, 32, 32).data;
      var buckets = {};
      for (var i = 0; i < d.length; i += 4) {
        if (d[i + 3] < 128) continue;
        var hsl = rgbToHsl(d[i], d[i + 1], d[i + 2]);
        if (hsl[2] < 0.10 || hsl[2] > 0.94) continue;
        var w = (hsl[1] + 0.08) * (1 - Math.abs(hsl[2] - 0.52) * 1.25);
        if (w <= 0.05) continue;
        var k = Math.round(hsl[0] * 18) % 18;
        var bk = buckets[k] || (buckets[k] = { w: 0, r: 0, g: 0, b: 0 });
        bk.w += w; bk.r += d[i] * w; bk.g += d[i + 1] * w; bk.b += d[i + 2] * w;
      }
      var best = null;
      Object.keys(buckets).forEach(function (key) { if (!best || buckets[key].w > best.w) best = buckets[key]; });
      if (!best || best.w < 1.5) return null;
      var h = rgbToHsl(best.r / best.w, best.g / best.w, best.b / best.w);
      return hslToRgb(h[0], clamp(h[1], 0.58, 0.90), clamp(h[2], 0.58, 0.70));
    } catch (e) {
      return null;
    }
  }
  var COVER_PROPS = [
    "--accent-rgb", "--accent-2-rgb", "--accent", "--accent-2",
    "--on-accent", "--art-hue", "--bg", "--bg-elev", "--glass-stage",
  ];
  function applyAccent(rgb) {
    if (!coverAccentOn || !rgb) {
      COVER_PROPS.forEach(function (p) { root.style.removeProperty(p); });
      body.classList.remove("cover-accent-on");
      if (el.meta) el.meta.setAttribute("content", (THEMES[curTheme] && THEMES[curTheme].meta) || "#080c15");
      return;
    }
    var hsl = rgbToHsl(rgb[0], rgb[1], rgb[2]);
    var isLight = curTheme === "daylight";
    var rgb1 = hslToRgb(hsl[0], clamp(hsl[1], 0.65, 0.92), isLight ? clamp(hsl[2], 0.42, 0.52) : clamp(hsl[2], 0.58, 0.70));
    var rgb2 = hslToRgb((hsl[0] + 0.11) % 1, clamp(hsl[1], 0.65, 0.92), isLight ? 0.48 : 0.64);
    root.style.setProperty("--accent-rgb", rgb1.join(", "));
    root.style.setProperty("--accent-2-rgb", rgb2.join(", "));
    root.style.setProperty("--accent", "rgb(" + rgb1.join(", ") + ")");
    root.style.setProperty("--accent-2", "rgb(" + rgb2.join(", ") + ")");
    var lum = (0.2126 * rgb1[0] + 0.7152 * rgb1[1] + 0.0722 * rgb1[2]) / 255;
    root.style.setProperty("--on-accent", lum > 0.55 ? "#06101b" : "#ffffff");
    root.style.setProperty("--art-hue", Math.round(hsl[0] * 360 - 200) + "deg");
    if (!isLight) {
      var bgRgb = hslToRgb(hsl[0], clamp(hsl[1] * 0.55, 0.22, 0.48), 0.06);
      var elevRgb = hslToRgb(hsl[0], clamp(hsl[1] * 0.55, 0.22, 0.50), 0.10);
      var stTop = hslToRgb(hsl[0], clamp(hsl[1] * 0.65, 0.28, 0.58), 0.16);
      var stBot = hslToRgb((hsl[0] + 0.06) % 1, clamp(hsl[1] * 0.55, 0.22, 0.48), 0.08);
      root.style.setProperty("--bg", "rgb(" + bgRgb.join(",") + ")");
      root.style.setProperty("--bg-elev", "rgb(" + elevRgb.join(",") + ")");
      root.style.setProperty("--glass-stage", "linear-gradient(165deg, rgba(" + stTop.join(",") + ", 0.78) 0%, rgba(" + stBot.join(",") + ", 0.90) 100%)");
      if (el.meta) el.meta.setAttribute("content", "rgb(" + bgRgb.join(",") + ")");
    } else {
      ["--bg", "--bg-elev", "--glass-stage"].forEach(function (p) { root.style.removeProperty(p); });
      var lightMeta = hslToRgb(hsl[0], 0.35, 0.93);
      if (el.meta) el.meta.setAttribute("content", "rgb(" + lightMeta.join(",") + ")");
    }
    body.classList.add("cover-accent-on");
  }
  var wantedAccentRaw = "";
  function activeCoverRaw() {
    if (cur && cur.cover) return cur.cover;
    var q = queue();
    if (q[0] && q[0].cover) return q[0].cover;
    if (search.results[0] && search.results[0].cover) return search.results[0].cover;
    return "";
  }
  function sampleAccent(raw) {
    if (!coverAccentOn) { applyAccent(null); return; }
    raw = normCover(raw || activeCoverRaw());
    wantedAccentRaw = raw;
    if (!raw) {
      applyAccent(seedAccent((cur && (cur.name || cur.artist || cur.id)) || "kazam-vinyl"));
      return;
    }
    if (accentCache[raw]) { applyAccent(accentCache[raw]); return; }
    if (el.coverImg && el.coverImg.complete && el.coverImg.naturalWidth > 0) {
      var directRgb = pickAccent(el.coverImg);
      if (directRgb) {
        accentCache[raw] = directRgb;
        applyAccent(directRgb);
        return;
      }
    }
    var src = coverSameOrigin(raw, 64);
    var img = new Image();
    img.decoding = "async";
    img.onload = function () {
      var rgb = pickAccent(img) || seedAccent((cur && (cur.name || cur.artist || cur.id)) || raw);
      accentCache[raw] = rgb;
      if (coverAccentOn && wantedAccentRaw === raw) applyAccent(rgb);
    };
    img.onerror = function () {
      var fallback = seedAccent((cur && (cur.name || cur.artist || cur.id)) || raw);
      accentCache[raw] = fallback;
      if (coverAccentOn && wantedAccentRaw === raw) applyAccent(fallback);
    };
    img.src = src;
  }

  // ── backdrop: the cover, blurred; two layers so a change cross-fades ──
  var artFlip = false, artWanted = "";
  function setBackdrop(url) {
    artWanted = url;
    if (!url) { el.artA.classList.remove("on"); el.artB.classList.remove("on"); root.style.removeProperty("--art"); return; }
    var probe = new Image();
    probe.referrerPolicy = "no-referrer";
    probe.onload = function () {
      if (artWanted !== url) return;
      var next = artFlip ? el.artA : el.artB, prev = artFlip ? el.artB : el.artA;
      var css = 'url("' + url.replace(/"/g, "%22") + '")';
      next.style.backgroundImage = css;
      root.style.setProperty("--art", css);
      next.classList.add("on");
      prev.classList.remove("on");
      artFlip = !artFlip;
    };
    probe.src = url;
  }

  // ── now playing ──────────────────────────────────────────────────────
  var shownCover = null;
  function setCover(raw) {
    raw = normCover(raw);
    if (raw === shownCover) return;
    shownCover = raw;
    el.cover.classList.remove("has-art", "broken");
    el.miniCover.parentNode.classList.remove("broken");
    if (!raw) {
      el.coverImg.removeAttribute("src");
      el.miniCover.removeAttribute("src");
      setBackdrop("");
      applyAccent(null);
      return;
    }
    el.coverImg.setAttribute("data-fb", coverFallback(raw, 600));
    el.coverImg.src = coverSrc(raw, 600);
    el.miniCover.setAttribute("data-fb", coverFallback(raw, 128));
    el.miniCover.src = coverSrc(raw, 100);
    setBackdrop(coverSrc(raw, 200));
    sampleAccent(raw);
  }
  el.coverImg.classList.add("cimg");
  el.miniCover.classList.add("cimg");
  el.coverImg.addEventListener("load", function () { el.cover.classList.add("has-art"); });

  function qualityLabel(t) {
    if (!t) return "";
    var lv = String(t.level || "").toLowerCase();
    var br = Number(t.br) || 0;
    if (br > 0 && br < 10000) br *= 1000;
    if (t.cached && !lv) return "本地";
    var name = LEVEL_LABELS[lv] || (lv === "meting" ? "在线" : "");
    var kbps = br ? Math.round(br / 1000) + "k" : "";
    return [name, kbps].filter(Boolean).join(" · ");
  }

  function setStageLyricText(text) {
    if (el.npLyricLine) el.npLyricLine.textContent = text || "";
  }

  function paintNowPlaying() {
    var t = cur;
    body.classList.toggle("no-track", !t);
    el.mini.hidden = !t;
    el.npActions.hidden = !t;
    el.npTitle.textContent = t ? t.name || String(t.id) : "KAZAM";
    el.npArtist.textContent = t ? [t.artist || "未知歌手", t.album].filter(Boolean).join(" · ") : "搜一首歌开始吧";
    el.miniTitle.textContent = t ? t.name || String(t.id) : "";
    el.miniArtist.textContent = t ? t.artist || "" : "";
    document.title = t ? (t.name || t.id) + (t.artist ? " · " + t.artist : "") + " — KAZAM" : "KAZAM · 听歌";
    if (el.stageSourceLabel) {
      el.stageSourceLabel.textContent = !t ? "STUDIO READY" : S.source === "radio" ? "TODAY RADIO · FM" : "MY PLAYLIST · HI-FI";
    }
    if (!t) setStageLyricText("搜一首歌，开启高解析音乐之旅");
    var q = qualityLabel(t);
    el.npQuality.hidden = !q;
    el.npQuality.textContent = q;
    el.npQuality.classList.toggle("hq", /无损|Hi-Res|母带/.test(q));
    el.npCached.hidden = !(t && t.cached);
    setCover(t ? t.cover : "");
    if (view === "lyrics") updateSub();
  }

  // ── progress / time ──────────────────────────────────────────────────
  var drag = { on: false, frac: 0 };
  function paintProgress() {
    var d = audio.duration, t = audio.currentTime;
    var p = d > 0 && isFinite(d) ? clamp(t / d, 0, 1) : 0;
    if (!drag.on) {
      el.progFill.style.transform = "scaleX(" + p + ")";
      el.prog.style.setProperty("--prog", String(p));
      el.timeCur.textContent = fmt(t);
    }
    el.miniFill.style.transform = "scaleX(" + p + ")";
    el.prog.setAttribute("aria-valuenow", String(Math.round(p * 100)));
  }
  function paintBuffered() {
    var d = audio.duration;
    if (!(d > 0) || !audio.buffered.length) return;
    el.progBuf.style.transform = "scaleX(" + clamp(audio.buffered.end(audio.buffered.length - 1) / d, 0, 1) + ")";
  }
  function dragTo(e) {
    var r = el.prog.getBoundingClientRect();
    drag.frac = clamp((e.clientX - r.left) / r.width, 0, 1);
    el.progFill.style.transform = "scaleX(" + drag.frac + ")";
    el.prog.style.setProperty("--prog", String(drag.frac));
    el.timeCur.textContent = fmt(drag.frac * audio.duration);
  }
  function updateProgTip(e) {
    if (!el.progTip || !(audio.duration > 0) || !isFinite(audio.duration)) return;
    var r = el.prog.getBoundingClientRect();
    var frac = clamp((e.clientX - r.left) / r.width, 0, 1);
    el.progTip.hidden = false;
    el.progTip.style.left = (frac * 100) + "%";
    el.progTip.textContent = fmt(frac * audio.duration);
  }
  el.prog.addEventListener("pointerdown", function (e) {
    if (!(audio.duration > 0) || !isFinite(audio.duration)) return;
    drag.on = true;
    el.prog.classList.add("drag");
    try { el.prog.setPointerCapture(e.pointerId); } catch (_) {}
    dragTo(e);
    updateProgTip(e);
  });
  el.prog.addEventListener("pointermove", function (e) {
    updateProgTip(e);
    if (drag.on) dragTo(e);
  });
  el.prog.addEventListener("pointerleave", function () {
    if (el.progTip && !drag.on) el.progTip.hidden = true;
  });
  function endDrag() {
    if (!drag.on) return;
    drag.on = false;
    el.prog.classList.remove("drag");
    if (el.progTip) el.progTip.hidden = true;
    seekTo(drag.frac * audio.duration);
  }
  el.prog.addEventListener("pointerup", endDrag);
  el.prog.addEventListener("pointercancel", endDrag);
  el.prog.addEventListener("keydown", function (e) {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      seekTo(audio.currentTime + (e.key === "ArrowRight" ? 5 : -5));
    }
  });
  function seekTo(sec) {
    if (!(audio.duration > 0)) return;
    audio.currentTime = clamp(sec, 0, audio.duration - 0.2);
    paintProgress();
    syncLyric(true);
  }
  function seekWhenReady(sec) {
    function once() {
      audio.removeEventListener("loadedmetadata", once);
      try { audio.currentTime = clamp(sec, 0, Math.max(0, (audio.duration || sec + 1) - 1)); } catch (_) {}
    }
    audio.addEventListener("loadedmetadata", once);
  }

  // ── queue & playback ─────────────────────────────────────────────────
  function queue() { return S.source === "radio" ? S.radio : S.playlist; }
  function streamUrl(id, extra) { return "/api/stream/" + enc(id) + "?level=" + level + (extra || ""); }
  function resetShuffle() { bag = []; bagKey = ""; shuffleHist = []; }

  function nextIndex(consume) {
    var q = queue();
    if (q.length <= 1) return q.length ? 0 : -1;
    if (S.mode !== "shuffle") return (S.idx + 1) % q.length;
    // shuffle hears every song once a round; 上一首 walks back through history
    var key = S.source + ":" + q.length;
    if (bagKey !== key || !bag.length) {
      bag = [];
      for (var i = 0; i < q.length; i++) if (i !== S.idx) bag.push(i);
      for (var j = bag.length - 1; j > 0; j--) {
        var k = Math.floor(Math.random() * (j + 1)), tmp = bag[j];
        bag[j] = bag[k]; bag[k] = tmp;
      }
      bagKey = key;
    }
    return consume ? bag.shift() : bag[0];
  }

  function playIndex(source, idx, opts) {
    S.source = source === "radio" ? "radio" : "my";
    var q = queue();
    if (!q[idx]) return;
    S.idx = idx;
    startTrack(q[idx], opts);
  }

  function startTrack(t, opts) {
    opts = opts || {};
    var token = ++playToken;
    cur = Object.assign({}, t, { _retried: false });
    userPaused = false;
    S.pos = 0;
    paintNowPlaying();
    lyric.lines = [];
    lyric.state = "loading";
    renderLyrics();
    el.progFill.style.transform = "scaleX(0)";
    el.prog.style.setProperty("--prog", "0");
    el.progBuf.style.transform = "scaleX(0)";
    el.timeCur.textContent = "0:00";
    el.timeTot.textContent = fmtMs(cur.duration) || "0:00";
    audio.src = streamUrl(cur.id);
    audio.loop = S.mode === "repeat-one";
    if (opts.startAt > 1) seekWhenReady(opts.startAt);
    if (opts.autoplay !== false) {
      body.classList.add("loading");
      var p = audio.play();
      if (p && p.catch) {
        p.catch(function (e) {
          if (token !== playToken) return;
          body.classList.remove("loading");
          if (e && e.name === "NotAllowedError") toast("点一下播放键开始");
        });
      }
    }
    markCurrent();
    scheduleSave();
    updateMediaSession();
    loadMeta(token);
    loadLyrics(token);
  }

  function loadMeta(token) {
    var t = cur;
    api("/api/song/" + enc(t.id) + "?level=" + level + (t.duration ? "&duration=" + t.duration : "")).then(function (r) {
      if (token !== playToken || !r || !r.ok || !r.data) return;
      var d = r.data;
      ["name", "artist", "album", "cover", "br", "size", "level", "source", "type", "pageUrl", "streamUrl"].forEach(function (k) {
        if (d[k] !== undefined && d[k] !== null && d[k] !== "") cur[k] = d[k];
      });
      if (d.duration) cur.duration = d.duration;
      cur.cached = !!d.cached;
      syncIntoLists(cur);
      paintNowPlaying();
      updateMediaSession();
    });
  }

  function syncIntoLists(t) {
    var touched = {};
    [["playlist", S.playlist], ["radio", S.radio]].forEach(function (pair) {
      pair[1].forEach(function (p, i) {
        if (!sameId(p.id, t.id)) return;
        var next = Object.assign({}, p, {
          name: t.name || p.name, artist: t.artist || p.artist, album: t.album || p.album,
          cover: t.cover || p.cover, duration: t.duration || p.duration, cached: !!t.cached,
        });
        if (next.name !== p.name || next.artist !== p.artist || next.cover !== p.cover || next.cached !== p.cached) touched[pair[0]] = true;
        pair[1][i] = next;
      });
    });
    Object.keys(touched).forEach(renderView);
    if (Object.keys(touched).length) scheduleSave();
  }

  function togglePlay() {
    if (!audio.getAttribute("src")) {
      if (cur) {
        var q = queue();
        if (q[S.idx] && sameId(q[S.idx].id, cur.id)) playIndex(S.source, S.idx, { startAt: S.pos });
        else startTrack(cur, { startAt: S.pos });
      } else if (queue().length) {
        playIndex(S.source, 0);
      } else {
        el.search.focus();
        toast("先搜一首歌吧");
      }
      return;
    }
    if (audio.paused) {
      audio.play().catch(function () {});
    } else {
      userPaused = true;
      audio.pause();
    }
  }

  function next(auto) {
    var q = queue();
    if (!q.length) return;
    if (auto && S.mode === "repeat-one") { audio.currentTime = 0; audio.play().catch(function () {}); return; }
    var from = S.idx;
    var i = nextIndex(true);
    if (S.mode === "shuffle" && from >= 0) shuffleHist.push(from);
    playIndex(S.source, i);
  }

  function prev() {
    var q = queue();
    if (!q.length) return;
    if (audio.currentTime > 4) { seekTo(0); return; }
    var i = S.mode === "shuffle" && shuffleHist.length ? shuffleHist.pop() : (S.idx - 1 + q.length) % q.length;
    playIndex(S.source, i);
  }

  function handleEnded() {
    if (endedFor === playToken) return;
    endedFor = playToken;
    next(true);
  }

  function handleError() {
    if (!cur || !audio.getAttribute("src")) return;
    var token = playToken;
    if (!cur._retried) {
      // one more try: the server resolves a dead CDN link again
      cur._retried = true;
      var at = audio.currentTime || 0;
      audio.src = streamUrl(cur.id, "&retry=" + Date.now());
      if (at > 1) seekWhenReady(at);
      audio.play().catch(function () {});
      return;
    }
    body.classList.remove("loading");
    failStreak += 1;
    var q = queue();
    if (q.length < 2) { toast("这首暂时播不了"); return; }
    if (failStreak >= Math.min(5, q.length)) { toast("连续几首都播不了，先停一下"); failStreak = 0; return; }
    toast("「" + (cur.name || cur.id) + "」播不了，换下一首");
    setTimeout(function () { if (token === playToken) next(false); }, 700);
  }

  function prefetchNext() {
    var token = playToken;
    setTimeout(function () {
      if (token !== playToken || S.mode === "repeat-one") return;
      var q = queue();
      if (q.length < 2) return;
      var t = q[nextIndex(false)];
      if (!t || (cur && sameId(t.id, cur.id))) return;
      // warms the server's link for the next song so it starts at once
      fetch("/api/song/" + enc(t.id) + "?level=" + level).catch(function () {});
      var img = new Image();
      img.referrerPolicy = "no-referrer";
      img.src = coverSrc(t.cover, 600);
    }, 5000);
  }

  function setMode(m) {
    S.mode = MODES.indexOf(m) >= 0 ? m : "loop";
    var info = MODE_INFO[S.mode];
    useIcon(el.mode, info.icon);
    el.mode.title = info.label;
    el.mode.setAttribute("aria-label", "播放方式：" + info.label);
    el.mode.classList.toggle("on", S.mode !== "loop");
    audio.loop = S.mode === "repeat-one";
    resetShuffle();
  }

  // audio events
  audio.addEventListener("play", function () {
    userPaused = false;
    body.classList.add("playing");
    useIcon(el.play, "#i-pause"); useIcon(el.miniPlay, "#i-pause");
    el.play.setAttribute("aria-label", "暂停");
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "playing";
  });
  audio.addEventListener("playing", function () {
    body.classList.remove("loading");
    failStreak = 0;
    if (prefetchedFor !== playToken) { prefetchedFor = playToken; prefetchNext(); }
  });
  audio.addEventListener("waiting", function () { if (!audio.paused) body.classList.add("loading"); });
  audio.addEventListener("canplay", function () { body.classList.remove("loading"); });
  audio.addEventListener("pause", function () {
    body.classList.remove("playing");
    useIcon(el.play, "#i-play"); useIcon(el.miniPlay, "#i-play");
    el.play.setAttribute("aria-label", "播放");
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "paused";
    // some streams stop a hair before the end without an "ended" event
    var d = audio.duration;
    if (!userPaused && !audio.ended && d > 0 && isFinite(d) && d - audio.currentTime < 1.2) handleEnded();
    scheduleSave();
  });
  audio.addEventListener("ended", function () { if (audio.ended) handleEnded(); });
  audio.addEventListener("error", handleError);
  var posStamp = 0;
  audio.addEventListener("timeupdate", function () {
    paintProgress();
    syncLyric(false);
    var now = Date.now();
    if (now - posStamp > 1000) {
      posStamp = now;
      S.pos = audio.currentTime || 0;
      if ("mediaSession" in navigator && navigator.mediaSession.setPositionState && audio.duration > 0 && isFinite(audio.duration)) {
        try {
          navigator.mediaSession.setPositionState({ duration: audio.duration, position: Math.min(audio.currentTime, audio.duration), playbackRate: audio.playbackRate || 1 });
        } catch (_) {}
      }
    }
  });
  audio.addEventListener("progress", paintBuffered);
  audio.addEventListener("durationchange", function () {
    if (audio.duration > 0 && isFinite(audio.duration)) el.timeTot.textContent = fmt(audio.duration);
    paintBuffered();
  });

  // ── Media Session (lock screen, headphones) ──────────────────────────
  function updateMediaSession() {
    if (!("mediaSession" in navigator) || !cur || !window.MediaMetadata) return;
    var art = coverSrc(cur.cover, 512);
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: cur.name || String(cur.id),
        artist: cur.artist || "",
        album: cur.album || "KAZAM",
        artwork: art ? [{ src: new URL(art, location.href).href, sizes: "512x512" }] : [],
      });
    } catch (_) {}
  }
  if ("mediaSession" in navigator) {
    [
      ["play", function () { if (audio.paused) togglePlay(); }],
      ["pause", function () { if (!audio.paused) togglePlay(); }],
      ["previoustrack", prev],
      ["nexttrack", function () { next(false); }],
      ["seekto", function (d) { if (d && d.seekTime != null) seekTo(d.seekTime); }],
      ["seekbackward", function (d) { seekTo(audio.currentTime - ((d && d.seekOffset) || 10)); }],
      ["seekforward", function (d) { seekTo(audio.currentTime + ((d && d.seekOffset) || 10)); }],
    ].forEach(function (h) {
      try { navigator.mediaSession.setActionHandler(h[0], h[1]); } catch (_) {}
    });
  }

  // ── volume / level ───────────────────────────────────────────────────
  function paintVolume() {
    var pct = Math.round((audio.muted ? 0 : audio.volume) * 100);
    el.volSlider.value = String(pct);
    el.volSlider.style.setProperty("--pct", pct + "%");
    var silent = audio.muted || audio.volume === 0;
    useIcon(el.vol, silent ? "#i-mute" : "#i-volume");
    el.vol.setAttribute("aria-label", silent ? "取消静音" : "静音");
  }
  function setVolume(v) {
    if (IOS) return;                      // iOS fixes media volume; only mute works
    audio.volume = clamp(v, 0, 1);
    if (audio.volume > 0) audio.muted = false;
    paintVolume();
    scheduleSave();
  }
  el.volSlider.addEventListener("input", function () { setVolume(Number(el.volSlider.value) / 100); });
  el.vol.addEventListener("click", function () { audio.muted = !audio.muted; paintVolume(); scheduleSave(); });

  function setLevel(v, opts) {
    if (!LEVEL_LABELS[v]) return;
    var changed = v !== level;
    level = v;
    el.level.value = v;
    try { localStorage.setItem(LEVEL_KEY, v); } catch (_) {}
    if (opts && opts.silent) return;
    toast("音质：" + el.level.options[el.level.selectedIndex].text);
    if (changed && cur && audio.getAttribute("src")) {
      // same song at another quality, from the same moment
      var at = audio.currentTime, wasPlaying = !audio.paused;
      startTrack(cur, { startAt: at, autoplay: wasPlaying });
    }
  }
  el.level.addEventListener("change", function () { setLevel(el.level.value); });

  // ── lists ────────────────────────────────────────────────────────────
  function listFor(v) {
    return v === "search" ? search.results : v === "playlist" ? S.playlist : v === "radio" ? S.radio : v === "library" ? library.items : [];
  }
  function actBtn(act, icon, label, quick) {
    return '<button type="button" class="icon-btn' + (quick ? " quick" : "") + '" data-act="' + act + '" aria-label="' + label +
      '" title="' + label + '"><svg class="icon"><use href="' + icon + '"/></svg></button>';
  }
  function rowHTML(t, i, v) {
    var sortable = v === "playlist" || v === "radio";
    var quick = v === "playlist" ? actBtn("remove", "#i-x", "从列表移除", true)
      : v === "radio" ? actBtn("add", "#i-plus", "加入我的列表", true)
      : actBtn("add", "#i-plus", "加入我的列表", true);
    var sub = esc(t.artist || "未知歌手") + (t.album ? " · " + esc(t.album) : "");
    return '<div class="row" role="button" tabindex="0" data-i="' + i + '" data-id="' + esc(t.id) + '">' +
      (sortable ? '<span class="grip" draggable="true" title="拖动排序"><svg class="icon"><use href="#i-grip"/></svg></span>' : "") +
      '<span class="idx"><b>' + (i + 1) + '</b><span class="eq"><i></i><i></i><i></i></span></span>' +
      '<span class="thumb">' + imgTag(t.cover, 100, 46) + "</span>" +
      '<span class="txt"><b>' + esc(t.name || t.id) + (admin.on && t.cached ? '<span class="badge">缓存</span>' : "") + "</b><small>" + sub + "</small></span>" +
      '<span class="dur">' + fmtMs(t.duration) + "</span>" +
      '<span class="acts">' + quick + actBtn("more", "#i-more", "更多") + "</span></div>";
  }
  function emptyHTML(icon, title, hint, action) {
    return '<div class="empty"><svg class="icon"><use href="' + icon + '"/></svg><div>' + esc(title) + "</div>" +
      (hint ? "<small>" + esc(hint) + "</small>" : "") +
      (action ? '<button type="button" class="btn primary" data-act="' + action[0] + '">' + esc(action[1]) + "</button>" : "") + "</div>";
  }
  function discoverHTML() {
    var histSec = "";
    if (searchHist.length) {
      histSec = '<div class="discover-sec">' +
        '<div class="discover-sec-head"><span>最近搜索</span><button type="button" data-act="clear-hist">清空</button></div>' +
        '<div class="chip-list">' + searchHist.map(function (k) {
          return '<button type="button" class="chip" data-kw="' + esc(k) + '">' + esc(k) + "</button>";
        }).join("") + "</div></div>";
    }
    var moods = MOODS.map(function (m) {
      return '<button type="button" class="mood-card" style="--mood-color:' + (m[2] || "#f5b942") + '" data-kw="' + esc(m[0]) + '">' +
        "<b>" + esc(m[0]) + "</b><small>" + esc(m[1]) + "</small></button>";
    }).join("");
    return '<div class="discover">' +
      '<div class="discover-hero">' +
        '<button type="button" class="hero-card" data-act="radio">' +
          '<span class="hero-card-icon"><svg class="icon"><use href="#i-radio"/></svg></span>' +
          '<span class="hero-card-txt"><b>今日电台 · 随心听</b><small>每次随机挑一批精选好歌，不占你的列表</small></span>' +
        "</button>" +
        '<button type="button" class="hero-card secondary" data-act="import-netease">' +
          '<span class="hero-card-icon"><svg class="icon"><use href="#i-list-plus"/></svg></span>' +
          '<span class="hero-card-txt"><b>导入网易云歌单</b><small>粘贴歌单或单曲链接 / ID 一键同步解析</small></span>' +
        "</button>" +
      "</div>" +
      histSec +
      '<div class="discover-sec">' +
        '<div class="discover-sec-head"><span>灵感频道 · 点一下立即探索</span></div>' +
        '<div class="mood-grid">' + moods + "</div>" +
      "</div>" +
    "</div>";
  }
  function skeletonHTML(n) {
    var s = "";
    for (var i = 0; i < n; i++) s += '<div class="skeleton"></div>';
    return s;
  }
  function matchFilter(t, q) {
    if (!q) return true;
    var hay = ((t.name || "") + " " + (t.artist || "") + " " + (t.album || "") + " " + (t.id || "")).toLowerCase();
    return hay.indexOf(q) >= 0;
  }
  function renderView(v) {
    if (v === "lyrics") return;
    var box = views[v];
    var list = listFor(v);
    var html;
    if (v === "search") {
      if (search.state === "loading") html = skeletonHTML(8);
      else if (search.state === "error") html = emptyHTML("#i-search", "搜索失败", search.error || "上游暂时没有响应", ["retry-search", "再试一次"]);
      else if (!list.length && search.state === "done") html = emptyHTML("#i-search", "没有找到「" + search.kw + "」", "换个关键词试试，或粘贴网易云歌曲链接");
      else if (!list.length) html = discoverHTML();
    } else if (v === "playlist" && !list.length) {
      html = emptyHTML("#i-list-plus", "列表还是空的", "在搜索结果里点 ＋，或者导入一张网易云歌单", ["import-netease", "导入歌单"]);
    } else if (v === "radio" && !list.length) {
      html = radioLoading ? skeletonHTML(8) : emptyHTML("#i-radio", "今日电台还没开", "每次随机挑一批歌，不会动你的列表", ["radio", "开始今日电台"]);
    } else if (v === "library" && !list.length) {
      html = library.loading ? skeletonHTML(6) : emptyHTML("#i-library", "服务器上还没有缓存", "播放时点「缓存」把歌存到服务器");
    }
    if (!html) {
      var fq = (v === "playlist" || v === "radio" || v === "library") ? filterQuery.trim().toLowerCase() : "";
      var rows = [];
      list.forEach(function (t, i) {
        if (!fq || matchFilter(t, fq)) rows.push(rowHTML(t, i, v));
      });
      html = rows.length ? rows.join("") : emptyHTML("#i-search", "没有匹配的曲目", "换个筛选词试试");
    }
    box.innerHTML = html;
    if (v === "playlist") el.countMy.textContent = S.playlist.length ? String(S.playlist.length) : "";
    if (v === "radio") el.countRadio.textContent = S.radio.length ? String(S.radio.length) : "";
    markCurrent();
    if (v === view) updateSub();
  }
  function renderAll() { ["search", "playlist", "radio", "library"].forEach(renderView); }

  function markCurrent() {
    $$(".row.current", el.panelBody).forEach(function (r) { r.classList.remove("current"); });
    if (!cur) return;
    var sel = '.row[data-id="' + cssEsc(cur.id) + '"]';
    $$(sel, views.search).concat($$(sel, views.library)).forEach(function (r) { r.classList.add("current"); });
    var r = $('.row[data-i="' + S.idx + '"]', S.source === "radio" ? views.radio : views.playlist);
    if (r && sameId(r.getAttribute("data-id"), cur.id)) r.classList.add("current");
  }

  function fmtTotalDuration(list) {
    var ms = 0;
    for (var i = 0; i < list.length; i++) ms += Number(list[i].duration) || 0;
    if (!ms) return "";
    var mins = Math.round(ms / 60000);
    if (mins < 1) return "1 分钟内";
    if (mins < 60) return "共 " + mins + " 分钟";
    var h = Math.floor(mins / 60), m = mins % 60;
    return "共 " + h + " 小时" + (m ? " " + m + " 分" : "");
  }

  function updateSub() {
    var text = "";
    if (view === "search") {
      text = search.state === "loading" ? "正在搜索「" + search.kw + "」…"
        : search.state === "done" && search.results.length ? search.results.length + " 首 ·「" + search.kw + "」· 点一下就播"
        : "支持直接粘贴网易云单曲链接 / ID 秒级解析播放";
    } else if (view === "playlist") {
      var tot = fmtTotalDuration(S.playlist);
      text = S.playlist.length ? S.playlist.length + " 首" + (tot ? " · " + tot : "") + (isPhone() ? "" : " · 拖动把手或 Alt+↑↓ 排序") : "";
    } else if (view === "radio") {
      var rTot = fmtTotalDuration(S.radio);
      text = S.radio.length ? "今日电台" + (S.radioDate ? " · " + S.radioDate : "") + " · " + S.radio.length + " 首" + (rTot ? " · " + rTot : "") : "";
    } else if (view === "lyrics") {
      text = cur ? (cur.name || cur.id) + (cur.artist ? " — " + cur.artist : "") : "";
    } else if (view === "library") {
      text = library.items.length ? library.items.length + " 首已缓存在服务器" : "";
    }
    el.panelSub.textContent = text;
    if (el.panelBatch) {
      var batchList = (view === "search" || view === "radio" || view === "library") ? listFor(view) : [];
      el.panelBatch.hidden = !batchList.length;
    }
  }

  function setView(v) {
    if (v === "library" && !admin.on) v = "search";
    if (v !== "lyrics") lastListView = v;
    view = v;
    $$(".tabs [data-view]").forEach(function (b) { b.setAttribute("aria-selected", String(b.getAttribute("data-view") === v)); });
    Object.keys(views).forEach(function (k) { views[k].hidden = k !== v; });
    el.panelBody.classList.toggle("lyrics-on", v === "lyrics");
    $$(".panel-tools [data-for]").forEach(function (t) {
      t.hidden = t.getAttribute("data-for").split(" ").indexOf(v) < 0;
    });
    if (el.listFilter && filterQuery) {
      filterQuery = "";
      el.listFilter.value = "";
      if (v === "playlist" || v === "radio" || v === "library") renderView(v);
    }
    closeExport();
    closeMenu();
    if (v === "lyrics") { mountLyrics(); syncLyric(true); }
    if (v === "library" && !library.loaded) loadLibrary();
    updateSub();
  }
  $$(".tabs [data-view]").forEach(function (b) {
    b.addEventListener("click", function () { setView(b.getAttribute("data-view")); });
  });
  if (el.listFilter) {
    el.listFilter.addEventListener("input", function () {
      filterQuery = el.listFilter.value || "";
      if (view === "playlist" || view === "radio" || view === "library") renderView(view);
    });
  }
  if (el.btnPlayAll) {
    el.btnPlayAll.addEventListener("click", function () {
      var list = listFor(view);
      if (!list.length) return;
      if (view === "radio") {
        playIndex("radio", 0);
        toast("开始播放今日电台（" + list.length + " 首）");
        return;
      }
      var firstIdx = -1, added = 0;
      list.forEach(function (t, idx) {
        var at = findIndex(S.playlist, t.id);
        if (at < 0) {
          S.playlist.push(plain(t));
          at = S.playlist.length - 1;
          added++;
        }
        if (idx === 0) firstIdx = at;
      });
      resetShuffle();
      renderView("playlist");
      scheduleSave();
      if (firstIdx >= 0) playIndex("my", firstIdx);
      toast(added > 0 ? "正在播放全部（已将 " + added + " 首新歌加入列表）" : "正在播放全部");
    });
  }
  if (el.btnAddAll) {
    el.btnAddAll.addEventListener("click", function () {
      var list = listFor(view);
      if (!list.length) return;
      var added = 0;
      list.forEach(function (t) {
        if (findIndex(S.playlist, t.id) < 0) {
          S.playlist.push(plain(t));
          added++;
        }
      });
      if (added > 0) {
        resetShuffle();
        renderView("playlist");
        scheduleSave();
        toast("已将 " + added + " 首歌加入我的列表");
      } else {
        toast("当前列表歌曲已全部在我的列表中");
      }
    });
  }

  // list mutations
  function findIndex(list, id) {
    for (var i = 0; i < list.length; i++) if (sameId(list[i].id, id)) return i;
    return -1;
  }
  function plain(t) {
    return {
      id: t.id, name: t.name || String(t.id), artist: t.artist || "", album: t.album || "",
      cover: t.cover || "", duration: t.duration || 0, cached: !!t.cached,
    };
  }
  function addToMy(t, silent) {
    var i = findIndex(S.playlist, t.id);
    if (i < 0) {
      S.playlist.push(plain(t));
      i = S.playlist.length - 1;
      resetShuffle();
      renderView("playlist");
      scheduleSave();
      if (!silent) toast("已加入我的列表：" + (t.name || t.id));
    } else if (!silent) {
      toast("已经在我的列表里了");
    }
    return i;
  }
  function playNextSoon(t) {
    if (!cur) { var i0 = addToMy(t, true); playIndex("my", i0); return; }
    var source = S.source, list = queue();
    var at = findIndex(list, t.id);
    if (at === S.idx && sameId(list[at].id, cur.id)) { toast("正在播的就是这首"); return; }
    if (at >= 0) {
      var item = list.splice(at, 1)[0];
      if (at < S.idx) S.idx -= 1;
      list.splice(S.idx + 1, 0, item);
    } else {
      list.splice(S.idx + 1, 0, plain(t));
    }
    resetShuffle();
    renderView(source === "radio" ? "radio" : "playlist");
    scheduleSave();
    toast("下一首播放：" + (t.name || t.id));
  }
  function removeAt(v, i) {
    var list = v === "radio" ? S.radio : S.playlist;
    if (i < 0 || i >= list.length) return;
    var gone = list.splice(i, 1)[0];
    var live = (v === "radio") === (S.source === "radio");
    if (live) {
      if (i < S.idx) S.idx -= 1;
      else if (i === S.idx) S.idx = i - 1;   // the song keeps playing; the one that took its place comes next
    }
    resetShuffle();
    renderView(v);
    scheduleSave();
    toast("已移除：" + (gone.name || gone.id));
  }
  function moveTrack(v, from, to) {
    var list = v === "radio" ? S.radio : S.playlist;
    if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) return false;
    var item = list.splice(from, 1)[0];
    list.splice(to, 0, item);
    if ((v === "radio") === (S.source === "radio") && S.idx >= 0) {
      if (S.idx === from) S.idx = to;
      else if (from < S.idx && to >= S.idx) S.idx -= 1;
      else if (from > S.idx && to <= S.idx) S.idx += 1;
    }
    resetShuffle();
    renderView(v);
    scheduleSave();
    return true;
  }
  function playFrom(v, i) {
    var list = listFor(v), t = list[i];
    if (!t) return;
    if (v === "playlist") return playIndex("my", i);
    if (v === "radio") return playIndex("radio", i);
    playIndex("my", addToMy(t, true));
  }

  // row clicks, keys and menus (one listener for every list)
  el.panelBody.addEventListener("click", function (e) {
    var kwEl = e.target.closest("[data-kw]");
    if (kwEl) {
      var kw = kwEl.getAttribute("data-kw");
      el.search.value = kw;
      doSearch(kw);
      return;
    }
    var actEl = e.target.closest("[data-act]");
    var row = e.target.closest(".row");
    if (!row) { if (actEl) globalAct(actEl.getAttribute("data-act")); return; }
    var v = row.closest(".view").getAttribute("data-view");
    var i = Number(row.getAttribute("data-i"));
    if (actEl) {
      e.stopPropagation();
      var a = actEl.getAttribute("data-act");
      if (a === "more") openMenu(v, i, actEl);
      else rowAct(a, v, i);
      return;
    }
    if (e.target.closest(".grip")) return;
    playFrom(v, i);
  });
  el.panelBody.addEventListener("keydown", function (e) {
    var row = e.target.closest && e.target.closest(".row");
    if (!row || e.target !== row) return;
    var v = row.closest(".view").getAttribute("data-view");
    var i = Number(row.getAttribute("data-i"));
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); playFrom(v, i); }
    else if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown") && (v === "playlist" || v === "radio")) {
      e.preventDefault();
      var to = i + (e.key === "ArrowUp" ? -1 : 1);
      if (moveTrack(v, i, to)) { var moved = $('.row[data-i="' + to + '"]', views[v]); if (moved) moved.focus(); }
    } else if ((e.key === "Delete" || e.key === "Backspace") && (v === "playlist" || v === "radio")) {
      e.preventDefault();
      removeAt(v, i);
    }
  });

  function rowAct(a, v, i) {
    var t = listFor(v)[i];
    if (!t) return;
    if (a === "play") playFrom(v, i);
    else if (a === "add") addToMy(t);
    else if (a === "next") playNextSoon(t);
    else if (a === "up") moveTrack(v, i, i - 1);
    else if (a === "down") moveTrack(v, i, i + 1);
    else if (a === "remove") removeAt(v, i);
    else if (a === "download") downloadSong(t);
    else if (a === "share") copyText(publicBase + "/?play=" + enc(t.id), "已复制分享链接");
    else if (a === "cache") cacheTrack(t);
  }

  function globalAct(a) {
    if (a === "retry-search") doSearch(search.kw);
    else if (a === "import-netease") importNetease();
    else if (a === "radio") loadRadio();
    else if (a === "clear-hist") {
      searchHist = [];
      try { localStorage.removeItem("kazam.searchHist"); } catch (_) {}
      renderView("search");
      toast("已清空搜索历史");
    }
  }

  var menuCtx = null;
  function openMenu(v, i, anchor) {
    var list = listFor(v), t = list[i];
    if (!t) return;
    var items = [["play", "#i-play", "播放"]];
    if (v !== "playlist") items.push(["add", "#i-plus", "加入我的列表"]);
    items.push(["next", "#i-play-next", "下一首播放"]);
    if (v === "playlist" || v === "radio") {
      if (i > 0) items.push(["up", "#i-up", "上移"]);
      if (i < list.length - 1) items.push(["down", "#i-down", "下移"]);
    }
    items.push(["download", "#i-download", "下载（" + LEVEL_LABELS[level] + "）"]);
    items.push(["share", "#i-share", "复制分享链接"]);
    if (admin.on) items.push(["cache", "#i-cloud", t.cached ? "重新缓存到服务器" : "缓存到服务器"]);
    if (v === "playlist") items.push(["remove", "#i-trash", "从我的列表移除", "danger"]);
    if (v === "radio") items.push(["remove", "#i-trash", "从电台移除", "danger"]);
    el.menu.innerHTML = '<div class="menu-title">' + esc(t.name || t.id) + (t.artist ? " · " + esc(t.artist) : "") + "</div>" +
      items.map(function (it) {
        return '<button type="button" role="menuitem" data-mact="' + it[0] + '"' + (it[3] ? ' class="' + it[3] + '"' : "") +
          '><svg class="icon"><use href="' + it[1] + '"/></svg>' + esc(it[2]) + "</button>";
      }).join("");
    menuCtx = { v: v, i: i, id: t.id };
    el.menu.hidden = false;
    el.menu.classList.toggle("sheet", isPhone());
    if (!isPhone()) {
      var r = anchor.getBoundingClientRect();
      var mw = el.menu.offsetWidth, mh = el.menu.offsetHeight;
      var left = clamp(r.right - mw, 8, window.innerWidth - mw - 8);
      var top = r.bottom + 6;
      if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 6);
      el.menu.style.left = left + "px";
      el.menu.style.top = top + "px";
    } else {
      el.menu.style.left = "";
      el.menu.style.top = "";
    }
    var first = el.menu.querySelector("button");
    if (first) first.focus({ preventScroll: true });
  }
  function closeMenu() { if (!el.menu.hidden) { el.menu.hidden = true; menuCtx = null; } }
  el.menu.addEventListener("click", function (e) {
    var b = e.target.closest("[data-mact]");
    if (!b || !menuCtx) return;
    var ctx = menuCtx, list = listFor(ctx.v);
    var i = list[ctx.i] && sameId(list[ctx.i].id, ctx.id) ? ctx.i : findIndex(list, ctx.id);
    closeMenu();
    if (i >= 0) rowAct(b.getAttribute("data-mact"), ctx.v, i);
  });
  document.addEventListener("pointerdown", function (e) {
    if (!el.menu.hidden && !el.menu.contains(e.target) && !e.target.closest('[data-act="more"]')) closeMenu();
    if (!el.exportMenu.hidden && !e.target.closest(".tool-wrap")) closeExport();
  });
  el.panelBody.addEventListener("scroll", closeMenu, { passive: true });
  window.addEventListener("resize", closeMenu);

  // drag to reorder (desktop)
  var dragRow = null;
  el.panelBody.addEventListener("dragstart", function (e) {
    var grip = e.target.closest && e.target.closest(".grip");
    if (!grip) return;
    var row = grip.closest(".row");
    dragRow = { v: row.closest(".view").getAttribute("data-view"), from: Number(row.getAttribute("data-i")) };
    row.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(dragRow.from));
    try { e.dataTransfer.setDragImage(row, 24, 30); } catch (_) {}
  });
  function clearDrop() { $$(".row.drop-before, .row.drop-after", el.panelBody).forEach(function (r) { r.classList.remove("drop-before", "drop-after"); }); }
  el.panelBody.addEventListener("dragover", function (e) {
    if (!dragRow) return;
    var row = e.target.closest(".row");
    if (!row || row.closest(".view").getAttribute("data-view") !== dragRow.v) return;
    e.preventDefault();
    var r = row.getBoundingClientRect(), after = e.clientY > r.top + r.height / 2;
    if (!row.classList.contains(after ? "drop-after" : "drop-before")) {
      clearDrop();
      row.classList.add(after ? "drop-after" : "drop-before");
    }
  });
  el.panelBody.addEventListener("drop", function (e) {
    if (!dragRow) return;
    var row = e.target.closest(".row");
    e.preventDefault();
    if (row && row.closest(".view").getAttribute("data-view") === dragRow.v) {
      var r = row.getBoundingClientRect(), after = e.clientY > r.top + r.height / 2;
      var to = Number(row.getAttribute("data-i")) + (after ? 1 : 0);
      if (dragRow.from < to) to -= 1;
      moveTrack(dragRow.v, dragRow.from, clamp(to, 0, listFor(dragRow.v).length - 1));
    }
    clearDrop();
    dragRow = null;
  });
  el.panelBody.addEventListener("dragend", function () {
    clearDrop();
    $$(".row.dragging", el.panelBody).forEach(function (r) { r.classList.remove("dragging"); });
    dragRow = null;
  });

  // ── lyrics ───────────────────────────────────────────────────────────
  function parseTimed(text) {
    var map = new Map();
    var re = /\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]/g;
    String(text || "").split(/\r?\n/).forEach(function (line) {
      var stamps = [], m;
      re.lastIndex = 0;
      while ((m = re.exec(line))) stamps.push(parseInt(m[1], 10) * 60000 + Math.round(parseFloat(m[2].replace(":", ".")) * 1000));
      if (!stamps.length) return;
      var words = line.replace(re, "").trim();
      if (!words) return;
      stamps.forEach(function (ms) { if (!map.has(ms)) map.set(ms, words); });
    });
    return map;
  }
  function parseLrc(lrc, tlrc) {
    var lines = String(lrc || "").split(/\r?\n/);
    var mark = -1;
    for (var i = 0; i < lines.length; i++) if (TRANSLATION_MARK.test(lines[i])) { mark = i; break; }
    var orig = mark >= 0 ? lines.slice(0, mark).join("\n") : String(lrc || "");
    var tran = mark >= 0 ? lines.slice(mark + 1).join("\n") : "";
    if (!tran) tran = tlrc || "";
    var om = parseTimed(orig), tm = parseTimed(tran);
    var keys = Array.from(new Set(Array.from(om.keys()).concat(Array.from(tm.keys())))).sort(function (a, b) { return a - b; });
    var out = keys.map(function (ms) { return { ms: ms, orig: om.get(ms) || "", tran: tm.get(ms) || "" }; })
      .filter(function (l) { return l.orig || l.tran; });
    if (!out.length) {
      // untimed text: shown, not followed
      out = String(orig).split(/\r?\n/).map(function (s) { return s.replace(/\[[^\]]*\]/g, "").trim(); })
        .filter(Boolean).map(function (s) { return { ms: -1, orig: s, tran: "" }; });
    }
    return out;
  }
  function loadLyrics(token) {
    var id = cur.id;
    api("/api/lyric/" + enc(id)).then(function (r) {
      if (token !== playToken) return;
      var d = r && r.ok && r.data;
      lyric.lines = d ? parseLrc(d.lrc || d.lyric || "", d.tlrc || d.tlyric || "") : [];
      lyric.state = "done";
      renderLyrics();
    });
  }
  function renderLyrics() {
    var html;
    if (!cur) {
      html = emptyHTML("#i-lyrics", "还没有在播的歌");
      setStageLyricText("搜一首歌，开启高解析音乐之旅");
    } else if (lyric.state === "idle") {
      html = emptyHTML("#i-lyrics", "开始播放后显示歌词");
      setStageLyricText("点击播放 · 同步显示歌词");
    } else if (lyric.state === "loading") {
      html = '<div class="empty"><small>歌词加载中…</small></div>';
      setStageLyricText("正在载入同步歌词…");
    } else if (!lyric.lines.length) {
      html = emptyHTML("#i-lyrics", "这首歌暂时没有歌词", "可能是纯音乐，或者上游没有提供");
      setStageLyricText("纯音乐或暂无歌词 · 尽享旋律");
    } else {
      html = lyric.lines.map(function (l, i) {
        return '<p data-i="' + i + '">' + esc(l.orig || l.tran) + (l.orig && l.tran ? "<small>" + esc(l.tran) + "</small>" : "") + "</p>";
      }).join("");
      var first = lyric.lines[0];
      setStageLyricText((first.orig || first.tran || "♪") + (first.orig && first.tran ? " · " + first.tran : ""));
    }
    el.lyrics.innerHTML = html;
    el.lyrics.scrollTop = 0;
    lyric.idx = -1;
    syncLyric(true);
  }
  function lyricsVisible() {
    return el.lyrics.offsetParent !== null && (view === "lyrics" || body.classList.contains("sheet-lyrics"));
  }
  function syncLyric(force) {
    var lines = lyric.lines;
    if (!lines.length || lines[0].ms < 0) return;
    var pos = audio.currentTime * 1000 + 200;
    var lo = 0, hi = lines.length - 1, i = -1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (lines[mid].ms <= pos) { i = mid; lo = mid + 1; } else hi = mid - 1;
    }
    if (i === lyric.idx && !force) return;
    var old = el.lyrics.querySelector("p.on");
    if (old) old.classList.remove("on");
    lyric.idx = i;
    if (i < 0) return;
    var curLine = lines[i];
    if (curLine) {
      var lineTxt = (curLine.orig || curLine.tran || "") + (curLine.orig && curLine.tran ? " · " + curLine.tran : "");
      if (lineTxt) setStageLyricText(lineTxt);
    }
    var p = el.lyrics.children[i];
    if (!p) return;
    p.classList.add("on");
    if (!lyricsVisible() || (!force && Date.now() < lyric.holdUntil)) return;
    var top = p.offsetTop - el.lyrics.clientHeight * 0.38 + p.offsetHeight / 2;
    el.lyrics.scrollTo({ top: Math.max(0, top), behavior: force || reduceMotion ? "auto" : "smooth" });
  }
  ["wheel", "touchstart"].forEach(function (ev) {
    el.lyrics.addEventListener(ev, function () { lyric.holdUntil = Date.now() + 3500; }, { passive: true });
  });
  el.lyrics.addEventListener("click", function (e) {
    var p = e.target.closest("p[data-i]");
    if (!p) return;
    var l = lyric.lines[Number(p.getAttribute("data-i"))];
    if (!l || l.ms < 0) return;
    lyric.holdUntil = 0;
    seekTo(l.ms / 1000);
    if (audio.paused && audio.getAttribute("src")) audio.play().catch(function () {});
  });
  function mountLyrics() {
    var home = isPhone() && body.classList.contains("sheet-lyrics") ? el.stageLyrics : el.viewLyrics;
    if (el.lyrics.parentNode !== home) home.appendChild(el.lyrics);
  }

  // ── phone sheet ──────────────────────────────────────────────────────
  function openSheet(withLyrics) {
    if (!isPhone()) { if (withLyrics) setView("lyrics"); return; }
    if (!body.classList.contains("sheet-open")) {
      body.classList.add("sheet-open");
      try { window.history.pushState({ kazamSheet: 1 }, ""); } catch (_) {}
    }
    setSheetLyrics(!!withLyrics);
  }
  function closeSheet(fromHistory) {
    if (!body.classList.contains("sheet-open")) return;
    body.classList.remove("sheet-open");
    setSheetLyrics(false);
    if (!fromHistory && window.history.state && window.history.state.kazamSheet) {
      try { window.history.back(); } catch (_) {}
    }
  }
  function setSheetLyrics(on) {
    body.classList.toggle("sheet-lyrics", on);
    el.stageLyrics.hidden = !on;
    $("#btnSheetLyrics").setAttribute("aria-pressed", String(on));
    $("#btnSheetLyrics").classList.toggle("on", on);
    mountLyrics();
    if (on) syncLyric(true);
  }
  window.addEventListener("popstate", function () { closeSheet(true); });
  phoneQuery.addEventListener && phoneQuery.addEventListener("change", function () {
    if (!isPhone()) { body.classList.remove("sheet-open"); setSheetLyrics(false); }
    mountLyrics();
    updateSub();
  });
  $("#miniOpen").addEventListener("click", function () { openSheet(false); });
  $("#btnSheetClose").addEventListener("click", function () { closeSheet(false); });
  $("#btnSheetLyrics").addEventListener("click", function () { setSheetLyrics(!body.classList.contains("sheet-lyrics")); });
  $("#btnLyrics").addEventListener("click", function () { setView(view === "lyrics" ? lastListView : "lyrics"); });
  if (el.npLyricTicker) {
    el.npLyricTicker.addEventListener("click", function () {
      if (isPhone()) setSheetLyrics(!body.classList.contains("sheet-lyrics"));
      else setView(view === "lyrics" ? lastListView : "lyrics");
    });
  }
  if (el.cover) {
    el.cover.addEventListener("click", function () {
      if (isPhone()) setSheetLyrics(!body.classList.contains("sheet-lyrics"));
      else setView(view === "lyrics" ? lastListView : "lyrics");
    });
  }

  // ── search / parse ───────────────────────────────────────────────────
  function pushSearchHist(kw) {
    if (!kw || LINKISH.test(kw)) return;
    searchHist = [kw].concat(searchHist.filter(function (x) { return x !== kw; })).slice(0, 10);
    try { localStorage.setItem("kazam.searchHist", JSON.stringify(searchHist)); } catch (_) {}
  }
  el.search.addEventListener("input", function () {
    var v = (el.search.value || "").trim();
    if (el.searchBadge) el.searchBadge.hidden = !LINKISH.test(v);
    if (!v && search.state !== "idle") {
      search.state = "idle";
      search.results = [];
      search.kw = "";
      if (view === "search") renderView("search");
    }
  });
  if (el.npArtist) {
    el.npArtist.addEventListener("click", function () {
      if (!cur || !cur.artist) return;
      var a = String(cur.artist).split(/[\/、,&]/)[0].trim();
      if (a) { el.search.value = a; doSearch(a); }
    });
  }
  function doSearch(raw) {
    var kw = String(raw || "").trim();
    if (!kw) { el.search.focus(); return; }
    if (isPhone()) el.search.blur();
    if (LINKISH.test(kw)) {
      resolveAndPlay(kw, { quiet: true }).then(function (t) { if (!t) runSearch(kw); });
      return;
    }
    runSearch(kw);
  }
  function runSearch(kw) {
    var token = ++search.token;
    pushSearchHist(kw);
    search.kw = kw;
    search.state = "loading";
    search.results = [];
    setView("search");
    renderView("search");
    el.panelBody.scrollTop = 0;
    api("/api/search?q=" + enc(kw), { timeout: 25000 }).then(function (r) {
      if (token !== search.token) return;
      search.results = r.ok && Array.isArray(r.data) ? r.data : [];
      search.state = r.ok ? "done" : "error";
      search.error = r.error || "";
      renderView("search");
    });
  }
  function resolveAndPlay(raw, opts) {
    opts = opts || {};
    toast("正在解析…");
    return api("/api/parse?q=" + enc(raw)).then(function (r) {
      if (!r.ok || !r.data) { if (!opts.quiet) toast("解析失败：" + (r.error || "找不到这首歌")); return null; }
      var d = r.data;
      var t = plain({ id: d.id, name: d.name, artist: d.artist, album: d.album, cover: d.cover, duration: d.duration, cached: d.cached });
      var i = addToMy(t, true);
      S.playlist[i] = Object.assign({}, S.playlist[i], t);
      setView("playlist");
      renderView("playlist");
      toast("已加入我的列表：" + t.name);
      if (opts.play !== false) playIndex("my", i, { autoplay: opts.autoplay !== false });
      return t;
    });
  }
  el.searchForm.addEventListener("submit", function (e) { e.preventDefault(); doSearch(el.search.value); });

  // ── today radio ──────────────────────────────────────────────────────
  function loadRadio() {
    if (radioLoading) return;
    radioLoading = true;
    var buttons = [$("#btnRadio"), $("#btnReloadRadio")];
    buttons.forEach(function (b) { b.disabled = true; });
    var exclude = S.radio.map(function (t) { return t.id; }).filter(Boolean).slice(0, 80).join(",");
    var hadList = S.radio.length > 0;
    setView("radio");
    if (!hadList) renderView("radio");
    toast(hadList ? "换一批…" : "正在挑歌…");
    api("/api/radio/today?limit=30" + (exclude ? "&exclude=" + enc(exclude) : ""), { timeout: 45000 }).then(function (r) {
      radioLoading = false;
      buttons.forEach(function (b) { b.disabled = false; });
      if (!r.ok || !r.data || !r.data.length) { renderView("radio"); toast("今日电台没开成：" + (r.error || "上游没有响应")); return; }
      S.radio = r.data.map(plain);
      S.radioDate = r.date || "";
      resetShuffle();
      renderView("radio");
      el.panelBody.scrollTop = 0;
      toast("新的一批 " + S.radio.length + " 首" + (r.overlap ? "（重复 " + r.overlap + " 首）" : ""));
      playIndex("radio", 0);
    });
  }
  $("#btnRadio").addEventListener("click", loadRadio);
  $("#btnReloadRadio").addEventListener("click", loadRadio);

  // ── dialogs ──────────────────────────────────────────────────────────
  var dlgDefault = "";
  function openDialog(o) {
    return new Promise(function (resolve) {
      el.dlgTitle.textContent = o.title || "";
      el.dlgDesc.textContent = o.desc || "";
      if (o.input) {
        el.dlgInput.hidden = false;
        el.dlgInput.type = o.input.type || "text";
        el.dlgInput.placeholder = o.input.placeholder || "";
        el.dlgInput.value = o.input.value || "";
      } else {
        el.dlgInput.hidden = true;
      }
      var buttons = o.buttons || [{ label: "取消", value: "" }, { label: "确定", value: "ok", kind: "primary" }];
      dlgDefault = (buttons.filter(function (b) { return b.kind === "primary"; })[0] || buttons[buttons.length - 1]).value;
      el.dlgActions.innerHTML = buttons.map(function (b) {
        return '<button class="btn ' + (b.kind || "") + '" value="' + esc(b.value) + '">' + esc(b.label) + "</button>";
      }).join("");
      function done() {
        el.dlg.removeEventListener("close", done);
        var v = el.dlg.returnValue;
        resolve(v ? { action: v, value: el.dlgInput.value.trim() } : null);
      }
      el.dlg.returnValue = "";
      el.dlg.addEventListener("close", done);
      el.dlg.showModal();
      if (o.input) setTimeout(function () { el.dlgInput.focus(); el.dlgInput.select(); }, 30);
    });
  }
  el.dlgInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); el.dlg.close(dlgDefault); }
  });
  function confirmBox(title, desc, okLabel) {
    return openDialog({ title: title, desc: desc, buttons: [{ label: "取消", value: "" }, { label: okLabel || "确定", value: "ok", kind: "danger" }] })
      .then(function (r) { return !!(r && r.action === "ok"); });
  }

  // ── playlist tools: clear / import / export / NetEase playlist ───────
  $("#btnClearPl").addEventListener("click", function () {
    if (!S.playlist.length) return;
    confirmBox("清空我的列表？", "今日电台不受影响。", "清空").then(function (ok) {
      if (!ok) return;
      S.playlist = [];
      if (S.source === "my") S.idx = -1;
      resetShuffle();
      renderView("playlist");
      scheduleSave();
      toast("我的列表已清空");
    });
  });
  $("#btnClearRadio").addEventListener("click", function () {
    if (!S.radio.length) return;
    confirmBox("清空今日电台？", "我的列表不受影响。", "清空").then(function (ok) {
      if (!ok) return;
      S.radio = [];
      S.radioDate = "";
      if (S.source === "radio") { S.idx = -1; S.source = "my"; }
      resetShuffle();
      renderView("radio");
      scheduleSave();
      toast("电台已清空");
    });
  });
  function importNetease() {
    openDialog({
      title: "导入网易云歌单",
      desc: "粘贴歌单链接或歌单 ID。",
      input: { placeholder: "https://music.163.com/playlist?id=…" },
      buttons: [{ label: "取消", value: "" }, { label: "追加到列表", value: "append" }, { label: "替换我的列表", value: "replace", kind: "primary" }],
    }).then(function (r) {
      if (!r || !r.value) return;
      var m = r.value.match(/[?&]id=(\d+)/) || r.value.match(/playlist\/(\d+)/) || r.value.match(/^\s*(\d+)\s*$/);
      if (!m) { toast("没认出歌单 ID"); return; }
      toast("正在读取歌单…");
      api("/api/playlist/" + m[1], { timeout: 45000 }).then(function (res) {
        if (!res.ok || !res.data || !res.data.length) { toast("歌单读取失败：" + (res.error || "可能是私密歌单")); return; }
        var tracks = res.data.map(plain);
        if (r.action === "replace") {
          S.playlist = tracks;
          if (S.source === "my") S.idx = -1;
        } else {
          tracks.forEach(function (t) { if (findIndex(S.playlist, t.id) < 0) S.playlist.push(t); });
        }
        resetShuffle();
        setView("playlist");
        renderView("playlist");
        scheduleSave();
        toast((r.action === "replace" ? "已载入 " : "已追加 ") + tracks.length + " 首");
      });
    });
  }
  $("#btnLoadPl").addEventListener("click", importNetease);

  function closeExport() { el.exportMenu.hidden = true; el.exportBtn.setAttribute("aria-expanded", "false"); }
  el.exportBtn.addEventListener("click", function () {
    var open = el.exportMenu.hidden;
    el.exportMenu.hidden = !open;
    el.exportBtn.setAttribute("aria-expanded", String(open));
  });
  el.exportMenu.addEventListener("click", function (e) {
    var b = e.target.closest("[data-export]");
    if (b) exportList(b.getAttribute("data-export"));
  });
  function stamp() {
    var d = new Date();
    return "" + d.getFullYear() + String(d.getMonth() + 1).padStart(2, "0") + String(d.getDate()).padStart(2, "0");
  }
  function downloadText(name, mime, text) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: mime }));
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
  }
  function exportList(kind) {
    closeExport();
    var isRadio = view === "radio";
    var tracks = (isRadio ? S.radio : S.playlist).map(plain);
    if (!tracks.length) { toast("列表是空的"); return; }
    var name = "kazam-" + (isRadio ? "radio" : "my") + "-" + stamp();
    if (kind === "m3u") {
      var lines = ["#EXTM3U"];
      tracks.forEach(function (t) {
        lines.push("#EXTINF:" + (t.duration ? Math.round(t.duration / 1000) : -1) + "," + (t.artist || "") + " - " + t.name);
        lines.push(publicBase + "/api/stream/" + enc(t.id));
      });
      downloadText(name + ".m3u", "audio/x-mpegurl", lines.join("\n") + "\n");
    } else if (kind === "txt") {
      downloadText(name + ".txt", "text/plain;charset=utf-8", tracks.map(function (t, i) { return i + 1 + ". " + t.name + " — " + t.artist; }).join("\n") + "\n");
    } else {
      downloadText(name + ".json", "application/json", JSON.stringify({
        app: "kazam", version: 2, kind: isRadio ? "radio" : "my", exportedAt: Date.now(), count: tracks.length, playlist: tracks,
      }, null, 2));
    }
    toast("已导出 " + tracks.length + " 首");
  }
  $("#btnImportPl").addEventListener("click", function () { $("#importFile").click(); });
  $("#importFile").addEventListener("change", function () {
    var file = this.files && this.files[0];
    this.value = "";
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () {
      try {
        var data = JSON.parse(reader.result);
        var list = Array.isArray(data) ? data : data && Array.isArray(data.playlist) ? data.playlist : null;
        if (!list) throw new Error("bad");
        var n = 0;
        list.forEach(function (t) {
          if (t && t.id && findIndex(S.playlist, t.id) < 0) { S.playlist.push(plain(t)); n++; }
        });
        resetShuffle();
        setView("playlist");
        renderView("playlist");
        scheduleSave();
        toast("导入了 " + n + " 首");
      } catch (e) {
        toast("这个文件读不出歌单");
      }
    };
    reader.readAsText(file);
  });

  // ── downloads / share ────────────────────────────────────────────────
  function downloadSong(t) {
    var a = document.createElement("a");
    a.href = "/api/download/" + enc(t.id) + "?level=" + level;
    a.download = ((t.artist ? t.artist + " - " : "") + (t.name || t.id)).replace(/[<>:"/\\|?*]/g, "_");
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast("开始下载（" + LEVEL_LABELS[level] + "）：" + (t.name || t.id));
  }
  function copyText(text, okMsg) {
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); toast(okMsg || "已复制"); } catch (e) { toast("复制失败"); }
      ta.remove();
    }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(function () { toast(okMsg || "已复制"); }, fallback);
    else fallback();
  }
  $("#btnDlSong").addEventListener("click", function () { if (cur) downloadSong(cur); });
  $("#btnDlLyric").addEventListener("click", function () {
    if (!cur) return;
    var a = document.createElement("a");
    a.href = "/api/download-lyric/" + enc(cur.id);
    document.body.appendChild(a);
    a.click();
    a.remove();
  });
  $("#btnShare").addEventListener("click", function () {
    if (!cur) return;
    var url = publicBase + "/?play=" + enc(cur.id);
    if (isPhone() && navigator.share) {
      navigator.share({ title: cur.name, text: (cur.name || "") + (cur.artist ? " — " + cur.artist : ""), url: url }).catch(function () {});
    } else copyText(url, "已复制分享链接");
  });

  // ── admin ────────────────────────────────────────────────────────────
  function adminFetch(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({}, opts.headers, { "X-Admin-Token": admin.token });
    return api(path, opts).then(function (r) {
      if (r._status === 401 || r._status === 403) { setAdmin(""); toast("管理员登录已失效"); }
      return r;
    });
  }
  var quotaTimer = 0;
  function setAdmin(token) {
    admin.token = token || "";
    admin.on = !!admin.token;
    try {
      if (admin.on) sessionStorage.setItem(ADMIN_TOKEN_KEY, admin.token);
      else sessionStorage.removeItem(ADMIN_TOKEN_KEY);
    } catch (_) {}
    body.classList.toggle("admin", admin.on);
    clearInterval(quotaTimer);
    if (admin.on) {
      refreshQuota();
      quotaTimer = setInterval(refreshQuota, 30000);
    } else if (view === "library") {
      setView("search");
    }
    renderAll();
    paintNowPlaying();
  }
  function renderQuota(j) {
    var text = !j || !j.ok ? "额度 --" : "今日 " + (j.used == null ? "--" : j.used) + " · 剩 " + (j.remaining == null ? "--" : j.remaining);
    el.quota.textContent = text;
    el.quota.classList.toggle("low", !!(j && j.remaining != null && Number(j.remaining) < 50));
    var tip = "ChKSz 今日额度（点击刷新）";
    if (j && j.limit) tip += " · 免费上限 " + j.limit;
    if (j && j.paidRemaining) tip += " · 付费剩余 " + j.paidRemaining;
    if (j && j.ok && !j.hasKey) tip += " · 未配置密钥";
    el.quota.title = tip;
    $("#upQuotaText").textContent = !j || !j.ok ? "今日已用 -- / 剩余 --" : "今日已用 " + (j.used == null ? "--" : j.used) + " / 剩余 " + (j.remaining == null ? "--" : j.remaining);
  }
  function refreshQuota() {
    if (!admin.on) return;
    adminFetch("/api/admin/chksz/usage").then(function (j) { if (j.ok) renderQuota(j); });
  }
  el.quota.addEventListener("click", refreshQuota);
  $("#upQuotaRefresh").addEventListener("click", refreshQuota);

  var logoTaps = 0, logoTimer = 0;
  $("#logoBtn").addEventListener("click", function (e) {
    e.preventDefault();
    if (admin.on) return;
    logoTaps += 1;
    clearTimeout(logoTimer);
    logoTimer = setTimeout(function () { logoTaps = 0; }, 2500);
    if (logoTaps < 5) return;
    logoTaps = 0;
    openDialog({
      title: "管理员", input: { type: "password", placeholder: "管理员密码" },
      buttons: [{ label: "取消", value: "" }, { label: "进入", value: "ok", kind: "primary" }],
    }).then(function (r) {
      if (!r || !r.value) return;
      api("/api/admin/login", { method: "POST", body: { password: r.value } }).then(function (j) {
        if (!j.ok || !j.token) { toast("密码不对"); return; }
        setAdmin(j.token);
        toast("已进入管理员模式");
      });
    });
  });
  $("#btnAdminExit").addEventListener("click", function () { setAdmin(""); toast("已退出管理员"); });

  function loadLibrary() {
    if (!admin.on || library.loading) return;
    library.loading = true;
    renderView("library");
    api("/api/library").then(function (r) {
      library.loading = false;
      library.loaded = true;
      library.items = r.ok && Array.isArray(r.data) ? r.data : [];
      renderView("library");
    });
  }
  $("#btnRefreshLib").addEventListener("click", function () { library.loaded = false; loadLibrary(); });

  function cacheTrack(t) {
    if (!admin.on) { toast("需要管理员"); return; }
    toast("正在缓存到服务器…");
    adminFetch("/api/cache/" + enc(t.id) + "?level=" + level, { method: "POST", timeout: 180000 }).then(function (r) {
      if (!r.ok) { if (r._status !== 401) toast("缓存失败：" + (r.error || "")); return; }
      var d = r.data || {};
      var patch = { id: t.id, cached: true, name: d.name || t.name, artist: d.artist || t.artist, cover: d.cover || t.cover };
      if (cur && sameId(cur.id, t.id)) { Object.assign(cur, patch); paintNowPlaying(); }
      syncIntoLists(Object.assign({}, t, patch));
      search.results.forEach(function (s) { if (sameId(s.id, t.id)) s.cached = true; });
      renderView("search");
      if (library.loaded) { library.loaded = false; if (view === "library") loadLibrary(); }
      toast("已缓存：" + (patch.name || t.id));
    });
  }
  $("#btnCacheSong").addEventListener("click", function () { if (cur) cacheTrack(cur); });
  $("#btnCopyUrl").addEventListener("click", function () {
    if (!cur) return;
    copyText(publicBase + "/api/stream/" + enc(cur.id) + "?level=" + level, "已复制播放地址（" + LEVEL_LABELS[level] + "）");
  });

  // upstream manager
  var up = { current: "", providers: [], probe: null };
  var dlgUp = $("#dlgUpstream");
  function renderUpstream() {
    var probeMap = {};
    if (up.probe && up.probe.results) up.probe.results.forEach(function (r) { probeMap[r.id] = r; });
    var auto = up.probe && up.probe.auto;
    $("#upList").innerHTML = up.providers.map(function (p) {
      var isCur = p.id === up.current;
      var pr = p.id === "auto" ? auto : probeMap[p.id];
      var alive = pr && p.id !== "auto" ? !!pr.alive : null;
      var badges = (isCur ? '<span class="up-badge cur">当前</span>' : "") +
        (alive === true ? '<span class="up-badge ok">可达</span>' : alive === false ? '<span class="up-badge bad">失败</span>' : "") +
        (p.kind && p.kind !== "auto" ? '<span class="up-badge">' + esc(p.kind) + "</span>" : "") +
        (p.builtin ? '<span class="up-badge">内置</span>' : "");
      var meta = "";
      if (pr && p.id !== "auto") {
        meta = '<div class="up-meta">' + (pr.alive ? "可用" : "不可用") + (pr.summary ? " · " + esc(pr.summary) : "") +
          (pr.ms != null ? " · " + pr.ms + " ms" : "") + " · 通过 " + (pr.ok_count || 0) + "/" + (pr.total || 0) + "</div>";
      } else if (p.id === "auto" && auto && auto.summary) {
        meta = '<div class="up-meta">' + esc(auto.summary) + "</div>";
      }
      var checks = pr && pr.checks && pr.checks.length ? '<div class="up-checks">' + pr.checks.map(function (c) {
        return '<span class="up-check' + (c.ok ? "" : " fail") + '" title="' + esc(c.ok ? c.detail || "" : c.error || "") + '">' +
          (c.ok ? "✓ " : "✗ ") + esc(c.label || c.name) + (c.ms != null ? " " + c.ms + "ms" : "") + "</span>";
      }).join("") + "</div>" : "";
      return '<div class="up-card' + (isCur ? " current" : "") + (alive === false ? " dead" : "") + '">' +
        '<div class="up-card-top">' + esc(p.name) + badges + "</div>" +
        '<div class="up-desc">' + esc(p.desc || "") + (p.endpoint ? "<br><code>" + esc(p.endpoint) + "</code>" : "") + "</div>" +
        meta + checks +
        '<div class="up-actions"><button type="button" class="btn small" data-use="' + esc(p.id) + '"' + (isCur ? " disabled" : "") + ">" +
        (isCur ? "使用中" : "选用此源") + "</button>" +
        (p.deletable ? '<button type="button" class="btn small danger" data-del="' + esc(p.id) + '">删除</button>' : "") + "</div></div>";
    }).join("") || '<div class="up-foot">加载中…</div>';
    $("#upRecommend").textContent = up.probe && up.probe.recommend
      ? "建议：" + up.probe.recommend + (up.probe.probe_ms ? "（整轮 " + up.probe.probe_ms + " ms）" : "")
      : "点「测速 / 测活」检查 VPS 到各上游的连通性与延迟。";
  }
  function upstreamState(j) {
    if (j.current) up.current = j.current;
    if (j.providers) up.providers = j.providers;
    renderUpstream();
  }
  $("#btnUpstream").addEventListener("click", function () {
    if (!admin.on) return;
    $("#upAddForm").hidden = true;
    dlgUp.showModal();
    renderUpstream();
    adminFetch("/api/admin/upstream").then(function (j) {
      if (!j.ok) { toast("读取上游配置失败"); return; }
      upstreamState(j);
      refreshQuota();
    });
  });
  $("#upClose").addEventListener("click", function () { dlgUp.close(); });
  dlgUp.addEventListener("click", function (e) { if (e.target === dlgUp) dlgUp.close(); });
  $("#upBtnProbe").addEventListener("click", function () {
    var btn = this, status = $("#upProbeStatus");
    btn.disabled = true;
    status.textContent = "测速中（10–20 秒）…";
    adminFetch("/api/admin/upstream/probe", { method: "POST", body: { sid: "416892104", keyword: "晴天" }, timeout: 60000 }).then(function (j) {
      btn.disabled = false;
      if (!j.ok) { status.textContent = "测速失败：" + (j.error || j._status); return; }
      up.probe = j;
      upstreamState(j);
      var alive = (j.results || []).filter(function (r) { return r.alive; }).length;
      status.textContent = "完成 · " + alive + "/" + (j.results || []).length + " 源可达 · " + (j.probe_ms || "?") + " ms";
    });
  });
  $("#upList").addEventListener("click", function (e) {
    var use = e.target.closest("[data-use]"), del = e.target.closest("[data-del]");
    if (use) {
      adminFetch("/api/admin/upstream", { method: "POST", body: { provider: use.getAttribute("data-use") } }).then(function (j) {
        if (!j.ok) { toast("切换失败：" + (j.error || j._status)); return; }
        upstreamState(j);
        toast("上游已切换为 " + up.current);
      });
    } else if (del) {
      var id = del.getAttribute("data-del");
      if (!window.confirm("删除源「" + id + "」？")) return;
      adminFetch("/api/admin/upstream/sources/" + enc(id), { method: "DELETE" }).then(function (j) {
        if (!j.ok) { toast("删除失败：" + (j.error || j._status)); return; }
        if (up.probe && up.probe.results) up.probe.results = up.probe.results.filter(function (r) { return r.id !== id; });
        upstreamState(j);
        toast("已删除 " + id);
      });
    }
  });
  $("#upBtnShowAdd").addEventListener("click", function () { var f = $("#upAddForm"); f.hidden = !f.hidden; });
  $("#upBtnAddCancel").addEventListener("click", function () { $("#upAddForm").hidden = true; });
  $("#upBtnAdd").addEventListener("click", function () {
    var endpoint = $("#upAddEndpoint").value.trim();
    if (!endpoint) { toast("请填写 Endpoint URL"); return; }
    adminFetch("/api/admin/upstream/sources", {
      method: "POST",
      body: { name: $("#upAddName").value.trim(), kind: $("#upAddKind").value, endpoint: endpoint, desc: $("#upAddDesc").value.trim() },
    }).then(function (j) {
      if (!j.ok) { toast("添加失败：" + (j.error || j._status)); return; }
      upstreamState(j);
      $("#upAddForm").hidden = true;
      ["#upAddEndpoint", "#upAddName", "#upAddDesc"].forEach(function (s) { $(s).value = ""; });
      toast("已添加 " + ((j.added && j.added.id) || "源"));
    });
  });

  // ── controls, vinyl mode, sleep timer & keys ─────────────────────────
  el.play.addEventListener("click", togglePlay);
  el.miniPlay.addEventListener("click", togglePlay);
  $("#btnNext").addEventListener("click", function () { next(false); });
  $("#miniNext").addEventListener("click", function () { next(false); });
  $("#btnPrev").addEventListener("click", prev);
  el.mode.addEventListener("click", function () {
    setMode(MODES[(MODES.indexOf(S.mode) + 1) % MODES.length]);
    toast(MODE_INFO[S.mode].label);
    scheduleSave();
  });

  function setTheme(id, silent) {
    if (!THEMES[id]) id = "aurora";
    curTheme = id;
    root.setAttribute("data-theme", id);
    if (el.themeList) {
      $$("[data-theme-opt]", el.themeList).forEach(function (btn) {
        btn.classList.toggle("active", btn.getAttribute("data-theme-opt") === id);
      });
    }
    try { localStorage.setItem(THEME_KEY, id); } catch (_) {}
    sampleAccent(cur ? cur.cover : "");
    if (!silent) toast("主题配色：" + THEMES[id].name);
  }
  function syncCoverAccentUI() {
    if (!el.btnCoverAccent) return;
    var val = coverAccentOn ? "true" : "false";
    el.btnCoverAccent.setAttribute("aria-pressed", val);
    el.btnCoverAccent.setAttribute("aria-checked", val);
    el.btnCoverAccent.classList.toggle("on", coverAccentOn);
  }
  function closeThemeMenu() {
    if (el.themeMenu) el.themeMenu.hidden = true;
    if (el.btnTheme) el.btnTheme.classList.remove("on");
  }
  if (el.btnTheme && el.themeMenu) {
    el.btnTheme.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = el.themeMenu.hidden;
      el.themeMenu.hidden = !open;
      el.btnTheme.classList.toggle("on", open);
    });
  }
  if (el.themeList) {
    el.themeList.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-theme-opt]");
      if (!btn) return;
      if (coverAccentOn) {
        coverAccentOn = false;
        syncCoverAccentUI();
        try { localStorage.setItem(COVER_ACCENT_KEY, "0"); } catch (_) {}
      }
      setTheme(btn.getAttribute("data-theme-opt"), false);
      closeThemeMenu();
    });
  }
  if (el.btnCoverAccent) {
    el.btnCoverAccent.addEventListener("click", function (e) {
      e.stopPropagation();
      coverAccentOn = !coverAccentOn;
      syncCoverAccentUI();
      try { localStorage.setItem(COVER_ACCENT_KEY, coverAccentOn ? "1" : "0"); } catch (_) {}
      sampleAccent(cur ? cur.cover : "");
      toast(coverAccentOn ? "已开启：跟随专辑封面动态取色" : "已关闭封面取色，恢复纯正主题色");
    });
  }
  document.addEventListener("click", function (e) {
    if (el.themeMenu && !el.themeMenu.hidden && !el.themeMenu.contains(e.target) && e.target !== el.btnTheme) {
      closeThemeMenu();
    }
  });

  function syncVinylUI() {
    var on = body.classList.contains("vinyl-on");
    if (el.vinylModeLabel) el.vinylModeLabel.textContent = on ? "黑胶唱机" : "专辑画幅";
    if (el.btnVinyl) el.btnVinyl.setAttribute("aria-pressed", String(on));
  }
  function toggleVinylMode() {
    var on = body.classList.toggle("vinyl-on");
    syncVinylUI();
    try { localStorage.setItem("kazam.vinylMode", on ? "1" : "0"); } catch (_) {}
    toast(on ? "已切换为黑胶唱机模式" : "已切换为方形专辑画幅");
  }
  if (el.btnVinyl) el.btnVinyl.addEventListener("click", toggleVinylMode);

  function toggleTheater() {
    var on = body.classList.toggle("theater-on");
    if (el.btnTheater) {
      el.btnTheater.classList.toggle("on", on);
      useIcon(el.btnTheater, on ? "#i-collapse" : "#i-expand");
      el.btnTheater.title = on ? "退出沉浸宽屏 (F)" : "沉浸宽屏模式 (F)";
    }
    if (on && view !== "lyrics") setView("lyrics");
    toast(on ? "已开启沉浸宽屏模式（按 F 或 Esc 退出）" : "已退出沉浸宽屏模式");
  }
  if (el.btnTheater) el.btnTheater.addEventListener("click", toggleTheater);

  function updateSleepUI() {
    if (!el.sleepBadge || !el.btnSleep) return;
    if (!sleepTimer.endAt) {
      el.sleepBadge.hidden = true;
      el.btnSleep.classList.remove("on");
      return;
    }
    var rem = Math.max(0, Math.ceil((sleepTimer.endAt - Date.now()) / 60000));
    if (rem <= 0) {
      clearInterval(sleepTimer.id);
      sleepTimer.endAt = 0;
      el.sleepBadge.hidden = true;
      el.btnSleep.classList.remove("on");
      if (!audio.paused) { userPaused = true; audio.pause(); toast("睡眠定时已到，已自动暂停播放"); }
      return;
    }
    el.sleepBadge.hidden = false;
    el.sleepBadge.textContent = rem + "m";
    el.btnSleep.classList.add("on");
  }
  function setSleepMinutes(mins) {
    clearInterval(sleepTimer.id);
    if (!mins || mins <= 0) {
      sleepTimer.endAt = 0;
      updateSleepUI();
      toast("已取消睡眠定时");
      return;
    }
    sleepTimer.endAt = Date.now() + mins * 60000;
    updateSleepUI();
    sleepTimer.id = setInterval(updateSleepUI, 15000);
    toast("将在 " + mins + " 分钟后自动暂停播放");
  }
  if (el.btnSleep) {
    el.btnSleep.addEventListener("click", function () {
      var btns = [
        { label: "取消", value: "" },
        { label: "15 分钟", value: "15" },
        { label: "30 分钟", value: "30" },
        { label: "60 分钟", value: "60", kind: "primary" },
      ];
      if (sleepTimer.endAt) btns.splice(1, 0, { label: "关闭定时", value: "0", kind: "danger" });
      openDialog({
        title: "睡眠定时关闭",
        desc: sleepTimer.endAt ? "当前定时运行中，到时将自动暂停音乐。" : "选择倒计时时长，到点后自动暂停播放。",
        buttons: btns,
      }).then(function (r) {
        if (!r || r.action === "") return;
        setSleepMinutes(Number(r.action));
      });
    });
  }

  function showShortcutsHelp() {
    openDialog({
      title: "快捷键与使用指南",
      desc: [
        "Space (空格)  —  播放 / 暂停",
        "← / →         —  上一首 / 下一首",
        "Shift + ← / → —  快退 / 快进 10 秒",
        "↑ / ↓         —  调节音量",
        "M             —  静音切换",
        "L             —  切换同步歌词视图",
        "F             —  沉浸宽屏模式 (左唱盘 + 右巨幕歌词)",
        "V             —  切换黑胶唱机 / 方形专辑画幅",
        "T             —  循环切换 5 套主题配色",
        "/             —  聚焦顶部搜索框",
        "Alt + ↑ / ↓   —  在列表中上下移动曲目",
        "连点左上角 KAZAM 5 次 — 进入管理员曲库模式",
      ].join("\n"),
      buttons: [{ label: "知道了", value: "ok", kind: "primary" }],
    });
  }
  if (el.btnShortcuts) el.btnShortcuts.addEventListener("click", showShortcutsHelp);

  document.addEventListener("keydown", function (e) {
    var tag = (e.target && e.target.tagName) || "";
    if (e.key === "Escape") {
      if (el.themeMenu && !el.themeMenu.hidden) { closeThemeMenu(); return; }
      if (!el.menu.hidden) { closeMenu(); return; }
      if (!el.exportMenu.hidden) { closeExport(); return; }
      if (body.classList.contains("sheet-open")) { closeSheet(false); return; }
      if (body.classList.contains("theater-on")) { toggleTheater(); return; }
    }
    if (/INPUT|SELECT|TEXTAREA/.test(tag) || e.metaKey || e.ctrlKey || document.querySelector("dialog[open]")) return;
    if (e.target.closest && e.target.closest(".row") && (e.key === "Enter" || e.altKey || e.key === "Delete" || e.key === "Backspace")) return;
    switch (e.key) {
      case " ":
        if (/BUTTON/.test(tag)) return;
        e.preventDefault(); togglePlay(); break;
      case "ArrowRight":
        if (e.shiftKey) seekTo(audio.currentTime + 10); else next(false);
        break;
      case "ArrowLeft":
        if (e.shiftKey) seekTo(audio.currentTime - 10); else prev();
        break;
      case "ArrowUp":
        if (!IOS && !e.altKey) { e.preventDefault(); setVolume(audio.volume + 0.05); }
        break;
      case "ArrowDown":
        if (!IOS && !e.altKey) { e.preventDefault(); setVolume(audio.volume - 0.05); }
        break;
      case "m": case "M":
        audio.muted = !audio.muted; paintVolume(); break;
      case "l": case "L":
        if (isPhone()) openSheet(!body.classList.contains("sheet-lyrics")); else setView(view === "lyrics" ? lastListView : "lyrics");
        break;
      case "f": case "F":
        if (!isPhone()) toggleTheater();
        break;
      case "v": case "V":
        toggleVinylMode();
        break;
      case "t": case "T":
        setTheme(THEME_ORDER[(THEME_ORDER.indexOf(curTheme) + 1) % THEME_ORDER.length], false);
        break;
      case "/":
        e.preventDefault(); el.search.focus(); el.search.select(); break;
      case "?":
        e.preventDefault(); showShortcutsHelp(); break;
    }
  });

  // ── persistence ──────────────────────────────────────────────────────
  var saveTimer = 0;
  function saveState() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        playlist: S.playlist, radioPlaylist: S.radio, radioDate: S.radioDate, playSource: S.source,
        curIdx: S.idx, mode: S.mode, volume: audio.volume, muted: audio.muted,
        position: audio.getAttribute("src") ? audio.currentTime || 0 : S.pos || 0, updatedAt: Date.now(),
      }));
    } catch (_) {}
  }
  function scheduleSave() { clearTimeout(saveTimer); saveTimer = setTimeout(saveState, 300); }
  function loadState() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY) || localStorage.getItem("kazam.vps.v1");
      if (!raw) return false;
      var s = JSON.parse(raw);
      var ok = function (t) { return t && t.id != null && t.id !== ""; };
      if (Array.isArray(s.playlist)) S.playlist = s.playlist.filter(ok).map(plain);
      if (Array.isArray(s.radioPlaylist)) S.radio = s.radioPlaylist.filter(ok).map(plain);
      S.radioDate = s.radioDate || "";
      S.source = s.playSource === "radio" ? "radio" : "my";
      S.idx = typeof s.curIdx === "number" ? s.curIdx : -1;
      S.pos = Number(s.position) || 0;
      if (MODES.indexOf(s.mode) >= 0) S.mode = s.mode;
      if (typeof s.volume === "number" && !IOS) audio.volume = clamp(s.volume, 0, 1);
      if (s.muted) audio.muted = true;
      var q = queue();
      if (S.idx >= q.length) S.idx = q.length ? 0 : -1;
      return true;
    } catch (e) {
      return false;
    }
  }
  window.addEventListener("pagehide", saveState);
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") saveState(); });

  // ── boot ─────────────────────────────────────────────────────────────
  if (IOS) body.classList.add("ios");
  try {
    if (localStorage.getItem("kazam.vinylMode") === "0") body.classList.remove("vinyl-on");
    var savedTheme = localStorage.getItem(THEME_KEY);
    if (THEMES[savedTheme]) curTheme = savedTheme;
    coverAccentOn = localStorage.getItem(COVER_ACCENT_KEY) === "1";
    var rawHist = JSON.parse(localStorage.getItem("kazam.searchHist") || "[]");
    if (Array.isArray(rawHist)) searchHist = rawHist.filter(Boolean).slice(0, 10);
  } catch (_) {}
  syncCoverAccentUI();
  setTheme(curTheme, true);
  syncVinylUI();
  try { var savedLevel = localStorage.getItem(LEVEL_KEY); if (LEVEL_LABELS[savedLevel]) level = savedLevel; } catch (_) {}
  setLevel(level, { silent: true });
  if (!loadState() && !IOS) audio.volume = 0.7;
  setMode(S.mode);
  paintVolume();
  try { var tok = sessionStorage.getItem(ADMIN_TOKEN_KEY); if (tok) { admin.token = tok; admin.on = true; body.classList.add("admin"); } } catch (_) {}
  var startQ = queue();
  if (startQ[S.idx]) cur = Object.assign({}, startQ[S.idx]);
  paintNowPlaying();
  if (cur) { lyric.state = "idle"; el.timeTot.textContent = fmtMs(cur.duration) || "0:00"; }
  renderLyrics();
  renderAll();
  if (admin.on) { refreshQuota(); quotaTimer = setInterval(refreshQuota, 30000); }

  var params = new URLSearchParams(location.search);
  var qParam = params.get("q"), playParam = params.get("play"), parseParam = params.get("parse");
  if (S.source === "radio" && S.radio.length) setView("radio");
  else if (S.playlist.length) setView("playlist");
  else setView("search");
  if (qParam) { el.search.value = qParam; doSearch(qParam); }
  if (parseParam) resolveAndPlay(parseParam);
  if (playParam) {
    var at = findIndex(S.playlist, playParam);
    if (at >= 0) { setView("playlist"); playIndex("my", at); }
    else resolveAndPlay(String(playParam));
  }
})();
