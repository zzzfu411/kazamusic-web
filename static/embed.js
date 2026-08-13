/* KAZAM embed mini player */
(function () {
  "use strict";
  var id = window.KAZAM_EMBED_ID || new URLSearchParams(location.search).get("id") || "";
  var autoplay =
    String(window.KAZAM_EMBED_AUTOPLAY || new URLSearchParams(location.search).get("autoplay") || "") ===
    "1";
  var base = (window.KAZAM_PUBLIC_BASE || location.origin).replace(/\/$/, "");

  var audio = document.getElementById("audio");
  var title = document.getElementById("title");
  var artist = document.getElementById("artist");
  var cover = document.getElementById("cover");
  var btn = document.getElementById("btnPlay");
  var openFull = document.getElementById("openFull");

  if (!id) {
    title.textContent = "缺少歌曲 ID";
    return;
  }

  openFull.href = base + "/?play=" + encodeURIComponent(id);
  audio.src = "/api/stream/" + id;

  fetch("/api/resolve/" + id)
    .then(function (r) {
      return r.json();
    })
    .then(function (j) {
      if (!j.ok || !j.data) return;
      title.textContent = j.data.name || id;
      artist.textContent = j.data.artist || "";
      if (j.data.cover) {
        cover.src = j.data.cover.indexOf("/api/") === 0
          ? j.data.cover
          : "/api/cover-proxy?url=" + encodeURIComponent(j.data.cover);
        cover.style.display = "block";
        cover.onerror = function () {
          cover.style.display = "none";
        };
      }
      if (j.data.cached) {
        artist.textContent = (artist.textContent || "") + " · 已缓存";
      }
    })
    .catch(function () {
      title.textContent = "ID " + id;
    });

  function syncBtn() {
    btn.textContent = audio.paused ? "▶ 播放" : "⏸ 暂停";
  }

  btn.onclick = function () {
    if (audio.paused) {
      audio.play().catch(function () {});
    } else {
      audio.pause();
    }
  };
  audio.onplay = syncBtn;
  audio.onpause = syncBtn;
  audio.onended = syncBtn;

  if (autoplay) {
    audio.play().catch(function () {});
  }
})();
