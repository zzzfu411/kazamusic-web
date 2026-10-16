/* KAZAM embed: one song, play/pause, progress, a link to the full player. */
(function () {
  "use strict";
  var params = new URLSearchParams(location.search);
  var id = window.KAZAM_EMBED_ID || params.get("id") || "";
  var autoplay = String(window.KAZAM_EMBED_AUTOPLAY || params.get("autoplay") || "") === "1";
  var base = (window.KAZAM_PUBLIC_BASE || location.origin).replace(/\/$/, "");
  var $ = function (s) { return document.getElementById(s); };
  var audio = $("audio"), title = $("title"), artist = $("artist"), cover = $("cover");

  if (!id) { title.textContent = "缺少歌曲 ID"; $("btnPlay").disabled = true; return; }
  $("openFull").href = base + "/?play=" + encodeURIComponent(id);
  audio.src = "/api/stream/" + encodeURIComponent(id);

  function coverUrl(raw, size) {
    if (!raw) return "";
    if (raw.indexOf("/api/cache/") === 0) return raw.split("?")[0] + "?size=" + (size <= 160 ? 160 : 480);
    if (raw.indexOf("/api/") === 0) return raw;
    if (/^https?:\/\/p\d+\.music\.126\.net\//i.test(raw)) return raw.replace(/^http:/i, "https:").split("?")[0] + "?param=" + size + "y" + size;
    return "/api/cover-proxy?url=" + encodeURIComponent(raw) + "&size=" + size;
  }

  fetch("/api/resolve/" + encodeURIComponent(id))
    .then(function (r) { return r.json(); })
    .then(function (j) {
      if (!j.ok || !j.data) { title.textContent = "ID " + id; return; }
      title.textContent = j.data.name || id;
      artist.textContent = j.data.artist || "";
      var src = coverUrl(j.data.cover, 160);
      if (src) {
        cover.onload = function () { cover.hidden = false; };
        cover.onerror = function () { cover.hidden = true; };
        cover.src = src;
        $("art").style.backgroundImage = 'url("' + src.replace(/"/g, "%22") + '")';
      }
    })
    .catch(function () { title.textContent = "ID " + id; });

  function sync() {
    $("iconPlay").hidden = !audio.paused;
    $("iconPause").hidden = audio.paused;
    $("btnPlay").setAttribute("aria-label", audio.paused ? "播放" : "暂停");
  }
  $("btnPlay").onclick = function () {
    if (audio.paused) audio.play().catch(function () {});
    else audio.pause();
  };
  audio.onplay = audio.onpause = audio.onended = sync;
  audio.ontimeupdate = function () {
    var d = audio.duration;
    $("fill").style.transform = "scaleX(" + (d > 0 && isFinite(d) ? audio.currentTime / d : 0) + ")";
  };
  $("bar").onclick = function (e) {
    var d = audio.duration, r = this.getBoundingClientRect();
    if (d > 0 && isFinite(d)) audio.currentTime = ((e.clientX - r.left) / r.width) * d;
  };
  if (autoplay) audio.play().catch(function () {});
})();
