/* Vitrine tablet app.
 *
 * One page for both roles. The agent tells the tablet whether it is the back
 * tablet (full control) or the pulpit (kiosk, options set from the back).
 * Everything live comes over the WebSocket from the agent, which reads it from
 * vMix, so every tablet always shows what is really on the projector.
 */
(function () {
  'use strict';

  var app = document.getElementById('app');
  var PER_PAGE = 6;
  var S = { boot: null, status: null, screens: [], wsOk: false, renderKey: '' };
  var ui = { set: 'all', page: 0, localSet: null, cue: null, drawer: false, drawerScreen: null, toast: '', toastTimer: null, lastError: '', marker: null };

  var ICON = {
    photo: '<svg class="ph-icon" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/></svg>',
    video: '<svg class="ph-icon" width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M10 8l6 4-6 4z"/></svg>',
    play: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 5l12 7-12 7z"/></svg>',
    pause: '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/></svg>',
    restart: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 103-6.7L3 8"/><path d="M3 3v5h5"/></svg>',
    more: '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>',
    left: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>',
    right: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>',
    back: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 010 10h-3"/></svg>',
    send: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    monitor: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg>',
    pulpit: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="On the pulpit"><rect x="4" y="3" width="16" height="12" rx="2"/><path d="M9 21l3-6 3 6"/></svg>',
    sun: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    moon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/></svg>',
    close: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    note: '<svg class="ph-icon" width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
    stop: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>',
    flag: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 21V4"/><path d="M5 4h11l-2 4 2 4H5"/></svg>',
    tablets: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="13" height="18" rx="2"/><path d="M20 7v13a1 1 0 01-1 1h-1"/></svg>'
  };

  /* ---------- helpers ---------- */

  function h(text) {
    return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmt(ms) {
    var total = Math.max(0, Math.floor((ms || 0) / 1000));
    var s = total % 60;
    return Math.floor(total / 60) + ':' + (s < 10 ? '0' + s : s);
  }
  function me() { return (S.boot && S.boot.screen) || { role: 'pulpit', options: {} }; }
  function isBack() { return me().role === 'back'; }
  function opts() { return me().options || {}; }
  function eng() { return (S.status && S.status.engine) || {}; }
  function sets() { return (S.boot && S.boot.sets) || []; }
  function asset(id) { return S.boot && S.boot.assets ? S.boot.assets[String(id)] : null; }
  function setById(id) { return sets().filter(function (s) { return s.id === id; })[0] || null; }
  function positions() { return (S.status && S.status.positions) || {}; }
  function settings() { return (S.boot && S.boot.settings) || {}; }
  function pulpits() { return S.screens.filter(function (s) { return s.role === 'pulpit'; }); }

  function post(path, body) {
    return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
      .then(function (r) {
        if (r.status === 401) { location.reload(); return null; }
        return r.json().then(function (data) {
          if (!r.ok) showToast(data.error || 'Something went wrong');
          return data;
        });
      })
      .catch(function () { showToast('The vMix PC is not reachable'); });
  }

  function showToast(text) {
    ui.toast = text;
    clearTimeout(ui.toastTimer);
    ui.toastTimer = setTimeout(function () { ui.toast = ''; render(); }, 6000);
    render();
  }

  function pulpitSet() {
    var screen = me();
    if (!isBack() && ui.localSet && setById(ui.localSet)) return setById(ui.localSet);
    return setById(screen.current_set_id) || sets()[0] || null;
  }
  function firstPulpitSet() {
    var p = pulpits()[0];
    return p ? (setById(p.current_set_id) || sets()[0] || null) : null;
  }

  function itemsBack() {
    var ids = [];
    var seen = {};
    sets().forEach(function (set) {
      if (ui.set !== 'all' && set.id !== ui.set) return;
      set.items.forEach(function (id) {
        if (!seen[id] && asset(id)) { seen[id] = true; ids.push({ id: id, set: set }); }
      });
    });
    return ids;
  }
  function itemsPulpit() {
    var set = pulpitSet();
    if (!set) return [];
    return set.items.filter(function (id) { return asset(id); }).map(function (id) { return { id: id, set: set }; });
  }
  function paged(list) {
    var count = Math.max(1, Math.ceil(list.length / PER_PAGE));
    if (ui.page > count - 1) ui.page = count - 1;
    if (ui.page < 0) ui.page = 0;
    return { items: list.slice(ui.page * PER_PAGE, ui.page * PER_PAGE + PER_PAGE), count: count };
  }

  function theme() {
    var t = me().theme;
    if (t === 'same' || !t) {
      var back = S.screens.filter(function (s) { return s.role === 'back'; })[0];
      t = back && back.theme !== 'same' ? back.theme : 'dark';
    }
    return t === 'light' ? 'light' : 'dark';
  }

  /* ---------- connection ---------- */

  function boot() {
    return fetch('/api/bootstrap', { cache: 'no-store' })
      .then(function (r) {
        if (r.status === 401) { location.reload(); throw new Error('unpaired'); }
        return r.json();
      })
      .then(function (data) {
        S.boot = data;
        S.screens = data.screens || [];
        if (data.status) S.status = data.status;
        render();
      })
      .catch(function () { setTimeout(boot, 3000); });
  }

  var ws = null;
  var wsRetry = 1000;
  function connect() {
    var proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
    ws = new WebSocket(proto + location.host + '/ws');
    ws.onopen = function () { S.wsOk = true; wsRetry = 1000; boot(); };
    ws.onclose = function (e) {
      S.wsOk = false;
      render();
      if (e.code === 4401) { location.reload(); return; }
      setTimeout(connect, wsRetry);
      wsRetry = Math.min(wsRetry * 2, 10000);
    };
    ws.onmessage = function (e) {
      var msg;
      try { msg = JSON.parse(e.data); } catch (err) { return; }
      if (msg.type === 'state') onStatus(msg);
      else if (msg.type === 'catalog') boot();
      else if (msg.type === 'screens') onScreens(msg.screens || []);
      else if (msg.type === 'settings') { if (S.boot) { S.boot.settings = msg.settings; render(); } }
      else if (msg.type === 'command') onCommand(msg);
    };
  }

  function audioState() { return eng().audio || {}; }
  function isTimedKind(kind) { return kind === 'video' || kind === 'audio'; }

  /* What the playback bar controls: the live video (or audio shown as its card),
     otherwise an audio file playing behind the default input. */
  function playing() {
    var e = eng();
    var au = audioState();
    if (e.asset_id && isTimedKind(e.kind)) {
      return { id: e.asset_id, position_ms: e.position_ms, duration_ms: e.duration_ms, paused: e.paused, jumping: e.jumping, audioOnly: false };
    }
    if (au.asset_id) {
      return { id: au.asset_id, position_ms: au.position_ms, duration_ms: au.duration_ms, paused: au.paused, jumping: false, audioOnly: true };
    }
    return null;
  }

  function liveKey() {
    var e = eng();
    var au = audioState();
    return [e.asset_id, e.pending_id, e.paused, e.jumping, e.error, e.vmix_ok, e.default_ok, e.mix, au.asset_id, au.pending_id, au.paused, S.status && S.status.server_online].join('|');
  }

  function onStatus(msg) {
    S.status = msg;
    var e = eng();
    if (e.error && e.error !== ui.lastError) {
      ui.lastError = e.error;
      showToast(e.error);
      return;
    }
    if (!e.error) ui.lastError = '';
    var key = liveKey();
    if (key !== S.renderKey) render();
    else updateProgress();
  }

  function onScreens(list) {
    S.screens = list;
    if (S.boot && S.boot.screen) {
      var mine = list.filter(function (s) { return s.id === S.boot.screen.id; })[0];
      if (mine) S.boot.screen = mine;
    }
    render();
  }

  function onCommand(msg) {
    if (msg.action === 'goto_page' && typeof msg.page === 'number') {
      ui.page = msg.page;
      ui.localSet = null;
      render();
    }
  }

  /* ---------- rendering ---------- */

  function tileState(id) {
    var e = eng();
    var au = audioState();
    if (e.pending_id === id || au.pending_id === id) return 'pending';
    if (e.asset_id === id || au.asset_id === id) return 'live';
    return '';
  }

  function progressPct() {
    var p = playing();
    if (!p || !p.duration_ms) return 0;
    return Math.min(100, Math.round((p.position_ms / p.duration_ms) * 100));
  }

  function renderTile(entry, index) {
    var a = asset(entry.id);
    var state = tileState(entry.id);
    var video = isTimedKind(a.kind);
    var sound = a.kind === 'audio';
    var audioOnly = sound && audioState().asset_id === entry.id;
    var back = isBack();
    var saved = positions()[String(entry.id)];
    var classes = 'tile' + (state ? ' ' + state : '') + (a.ready ? '' : ' notready');
    var label = (state === 'live' ? 'Take off ' : 'Show ') + a.name;
    var html = '<div class="' + classes + '">';
    html += '<button type="button" class="thumb" data-act="tap" data-id="' + entry.id + '" aria-pressed="' + (state === 'live') + '" aria-label="' + h(label) + '">';
    html += a.thumb ? '<img src="' + h(a.thumb) + '" alt="" draggable="false">' : (sound ? ICON.note : video ? ICON.video : ICON.photo);
    if (!back) html += '<span class="ov ov-num">' + (ui.page * PER_PAGE + index + 1) + '</span>';
    if (!a.ready) html += '<span class="ov ov-wait">Downloading…</span>';
    if (video && state !== 'live') html += '<span class="ov ov-dur">' + fmt(a.duration_ms) + '</span>';
    if (state === 'pending') html += '<span class="ov ov-tag pend"><span class="d"></span>SENDING…</span>';
    if (state === 'live') {
      if (audioOnly) html += '<span class="ov ov-tag live"><span class="d"></span>' + (back ? 'PLAYING · AUDIO' : 'PLAYING') + '</span>';
      else html += '<span class="ov ov-tag live"><span class="d"></span>' + (back ? 'LIVE · MIX ' + h(eng().mix) : 'ON SCREEN') + '</span>';
      if (back) html += '<span class="ov ov-hint">' + (audioOnly ? 'Tap again to stop' : 'Tap again to return to default') + '</span>';
      if (video) html += '<span class="ov ov-bar"><span id="tile-bar" style="width:' + progressPct() + '%"></span></span>';
    }
    if (!back && a.owner && entry.set && a.owner !== entry.set.guest && !(state === 'live' && video)) {
      html += '<span class="ov ov-from">from ' + h(a.owner) + '</span>';
    }
    html += '</button>';

    if (back) {
      html += '<div class="caption"><div class="txt"><div class="who">' + h(a.owner) + '</div>';
      if (opts().show_file_names !== false) html += '<div class="file">' + h(a.name) + '</div>';
      html += '</div>';
      if (video) {
        html += '<div class="row" style="flex-shrink:0;gap:6px">';
        if (saved && state !== 'live') html += '<button type="button" class="small-btn" data-act="resume" data-id="' + entry.id + '">' + ICON.play + ' Resume ' + fmt(saved) + '</button>';
        if (opts().video_seek) html += '<button type="button" class="small-btn sq" data-act="cue" data-id="' + entry.id + '" aria-label="Cue ' + h(a.name) + '">' + ICON.more + '</button>';
        html += '</div>';
      } else {
        html += '<span class="badge">Photo</span>';
      }
      html += '</div>';
    } else if (!video) {
      html += '<div class="strip"><span class="t">Photo</span></div>';
    } else {
      html += '<div class="strip"><span class="t">' + (sound ? 'Audio' : 'Video') + ' · ' + fmt(a.duration_ms) + '</span>';
      html += '<span class="row" style="gap:6px">';
      if (saved && state !== 'live') html += '<button type="button" class="small-btn" data-act="resume" data-id="' + entry.id + '">' + ICON.play + ' Resume ' + fmt(saved) + '</button>';
      if (opts().video_seek) html += '<button type="button" class="small-btn sq" data-act="cue" data-id="' + entry.id + '" aria-label="Cue ' + h(a.name) + '">' + ICON.more + '</button>';
      html += '</span></div>';
    }
    html += '</div>';
    return html;
  }

  function renderGrid(list, emptyText) {
    var page = paged(list);
    var html = '<main class="grid" id="grid">';
    if (!list.length) html += '<div class="empty">' + h(emptyText) + '</div>';
    page.items.forEach(function (entry, i) { html += renderTile(entry, i); });
    html += '</main>';
    return { html: html, count: page.count };
  }

  function renderTransport() {
    var e = eng();
    var p = playing();
    var a = p ? asset(p.id) : null;
    if (!a || !opts().video_basic) return '';
    var seek = opts().video_seek;
    var html = '<div class="transport">';
    if (p.audioOnly) html += '<button type="button" class="t-btn" data-act="audio-stop">' + ICON.stop + ' Stop audio</button>';
    html += '<button type="button" class="t-btn" data-act="seek" data-t="0">' + ICON.restart + ' From start</button>';
    if (seek) html += '<button type="button" class="t-btn" data-act="nudge" data-d="-10000" aria-label="Back 10 seconds">−10s</button>';
    if (p.paused) html += '<button type="button" class="t-btn' + (isBack() ? ' inv' : '') + '" data-act="play" aria-label="Play">' + ICON.play + (isBack() ? '' : ' Play') + '</button>';
    else html += '<button type="button" class="t-btn' + (isBack() ? ' inv' : '') + '" data-act="pause" aria-label="Pause">' + ICON.pause + (isBack() ? '' : ' Pause') + '</button>';
    if (seek) html += '<button type="button" class="t-btn" data-act="nudge" data-d="10000" aria-label="Forward 10 seconds">+10s</button>';
    if (seek) html += '<button type="button" class="t-btn" data-act="marker-live" aria-label="Add marker here">' + ICON.flag + ' Marker</button>';
    if (a.markers && a.markers.length) {
      html += '<span class="divider"></span>';
      a.markers.forEach(function (m) {
        html += '<button type="button" class="mk-btn" data-act="seek" data-t="' + m.t_ms + '">' + h(m.label) + ' · ' + fmt(m.t_ms) + '</button>';
      });
    }
    html += '<div class="progress"><div class="time-row"><span id="prog-time">' + fmt(p.position_ms) + ' / ' + fmt(p.duration_ms || a.duration_ms) + '</span><span id="prog-note">' + h(progressNote()) + '</span></div>';
    html += '<div class="track"><span id="prog-bar" style="width:' + progressPct() + '%"></span></div></div>';
    html += '</div>';
    return html;
  }

  function progressNote() {
    var p = playing();
    if (!p) return '';
    if (p.jumping) return 'Crossfading…';
    if (p.paused) return 'Paused';
    return fmt(Math.max(0, (p.duration_ms || 0) - (p.position_ms || 0))) + ' left';
  }

  function nowLabel() {
    var e = eng();
    var p = asset(e.pending_id);
    if (p) return { cls: 'pend', text: 'Sending ' + p.name + '…' };
    var a = asset(e.asset_id);
    var au = asset(audioState().asset_id);
    var sound = au ? ' · \u266A ' + au.name : '';
    if (a) return { cls: 'live', text: a.name + ' · ' + (isTimedKind(a.kind) ? progressNote() : a.owner) + sound };
    if (e.asset_id) return { cls: 'live', text: 'A file from another service' + sound };
    if (au) return { cls: 'live', text: '\u266A ' + au.name + ' · ' + progressNote() + ' (default stays on screen)' };
    return { cls: '', text: 'Default · ' + (settings().default_input_title || e.mix_active || 'not set') };
  }

  // "Youth night · Tue 25 Nov". The back tablet can switch between the upcoming events
  // the server sent; "Automatic" follows today's (or the next) event.
  function eventLabel(s) {
    if (!s) return 'No upcoming event';
    if (s.short_when) return s.name + ' · ' + s.short_when;
    var d = s.date ? new Date(s.date + 'T12:00:00') : null;
    var when = d && !isNaN(d) ? d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }) : '';
    return s.name + (when ? ' · ' + when : '');
  }

  function eventPicker(service) {
    var list = S.boot.services || [];
    if (list.length < 2) return '<div class="sub">' + h(eventLabel(service)) + '</div>';
    var pinned = S.boot.service_pinned;
    var html = '<label class="event-pick"><span class="sr-only">Event</span><select data-change="event">';
    html += '<option value="0"' + (pinned ? '' : ' selected') + '>Automatic: ' + h(eventLabel(pinned ? list[0] : service)) + '</option>';
    list.forEach(function (s) {
      html += '<option value="' + s.id + '"' + (pinned && service && service.id === s.id ? ' selected' : '') + '>' + h(eventLabel(s)) + '</option>';
    });
    return html + '</select></label>';
  }

  function renderBack() {
    var e = eng();
    var st = S.status || {};
    var html = '';
    var service = S.boot.service;
    var pSet = firstPulpitSet();
    var pOnline = pulpits().some(function (p) { return p.online; });
    html += '<header class="head"><div><div class="title">Vitrine' + (S.boot.location ? ' <span class="loc">· ' + h(S.boot.location.name) + '</span>' : '') + '</div>' + eventPicker(service) + '</div><div class="head-right">';
    html += '<span class="pill"><span class="dot ' + (st.server_online ? 'ok' : 'warn') + '"></span>' + (st.server_online ? 'Server online' : 'Server offline') + '</span>';
    html += '<span class="pill"><span class="dot ' + (e.vmix_ok ? 'ok' : 'bad') + '"></span>' + (e.vmix_ok ? 'vMix connected' : 'vMix not reachable') + '</span>';
    if (pulpits().length) html += '<span class="pill"><span class="dot ' + (pOnline ? 'ok' : 'warn') + '"></span>' + (pOnline ? 'Pulpit in sync' : 'Pulpit offline') + '</span>';
    html += '<span class="pill strong">' + ICON.monitor + ' Output: ' + (e.mix > 1 ? 'Mix ' + e.mix : 'Main') + '</span>';
    html += '<button type="button" class="icon-btn" data-act="drawer" aria-label="Tablets">' + ICON.tablets + '</button>';
    html += '<button type="button" class="icon-btn" data-act="theme" aria-label="' + (theme() === 'dark' ? 'Switch to light theme' : 'Switch to dark theme') + '">' + (theme() === 'dark' ? ICON.sun : ICON.moon) + '</button>';
    html += '</div></header>';

    html += '<nav class="nav" aria-label="Filter by guest"><div class="chips">';
    var total = itemsBackAll();
    html += '<button type="button" class="chip" data-act="chip" data-set="all" aria-pressed="' + (ui.set === 'all') + '">All guests <span class="count">' + total + '</span></button>';
    sets().forEach(function (set) {
      var onPulpit = pSet && pSet.id === set.id;
      html += '<button type="button" class="chip" data-act="chip" data-set="' + set.id + '" aria-pressed="' + (ui.set === set.id) + '">' + h(set.guest || set.title) +
        ' <span class="count">' + set.items.filter(function (id) { return asset(id); }).length + '</span>' + (onPulpit ? ICON.pulpit : '') + '</button>';
    });
    html += '</div>';
    if (pulpits().length) {
      var canSend = ui.set !== 'all' && (!pSet || pSet.id !== ui.set);
      var label = 'Pick a guest to send';
      if (ui.set !== 'all' && pSet && pSet.id === ui.set) label = 'Already on pulpit';
      if (canSend) label = 'Send ' + ((setById(ui.set) || {}).guest || 'set') + ' to pulpit';
      html += '<div class="nav-right"><div class="stack-right"><span class="label">Pulpit shows</span><strong>' + h(pSet ? pSet.guest || pSet.title : '—') + '</strong></div>';
      html += '<button type="button" class="btn ' + (canSend ? 'primary' : 'off') + '" data-act="send-pulpit"' + (canSend ? '' : ' disabled') + '>' + ICON.send + ' ' + h(label) + '</button></div>';
    }
    html += '</nav>';

    var grid = renderGrid(itemsBack(), sets().length ? 'No files for this guest yet.' : 'No sets in the active service yet.');
    html += grid.html;
    html += renderTransport();

    var now = nowLabel();
    html += '<footer class="foot"><div class="now"><span class="label">On ' + (e.mix > 1 ? 'Mix ' + e.mix : 'Main') + ' now</span><strong class="' + now.cls + '" id="now-label">' + h(now.text) + '</strong></div>';
    html += renderPager(grid.count);
    var anything = e.asset_id || e.pending_id;
    html += '<div class="end"><button type="button" class="default-btn" data-act="clear"' + (anything ? '' : ' disabled') + '>' + ICON.back + ' Back to default</button></div>';
    html += '</footer>';
    return html;
  }

  function itemsBackAll() {
    var seen = {};
    var n = 0;
    sets().forEach(function (set) { set.items.forEach(function (id) { if (!seen[id] && asset(id)) { seen[id] = true; n++; } }); });
    return n;
  }

  function renderPager(count) {
    return '<div class="pager"><button type="button" class="nav-btn" data-act="page" data-d="-1" aria-label="Previous page"' + (ui.page > 0 ? '' : ' disabled') + '>' + ICON.left + '</button>' +
      '<span class="n">' + (isBack() ? 'Page ' + (ui.page + 1) + ' of ' + count : (ui.page + 1) + ' / ' + count) + '</span>' +
      '<button type="button" class="nav-btn" data-act="page" data-d="1" aria-label="Next page"' + (ui.page < count - 1 ? '' : ' disabled') + '>' + ICON.right + '</button></div>';
  }

  function renderPulpit() {
    var e = eng();
    var o = opts();
    var set = pulpitSet();
    var html = '';
    var status = { cls: '', text: 'Screen shows the banner' };
    if (audioState().asset_id) status = { cls: 'live', text: 'Audio is playing' };
    if (e.asset_id) status = { cls: 'live', text: e.kind === 'video' ? 'A video is on screen' : e.kind === 'audio' ? 'Audio is playing' : 'A picture is on screen' };
    if (e.pending_id || audioState().pending_id) status = { cls: 'pend', text: 'Sending to the screen…' };
    html += '<header class="p-head"><div class="title">' + (o.show_title !== false && set ? h(set.title) : '') +
      (o.show_title !== false && S.boot.service ? '<div class="p-sub">' + h(eventLabel(S.boot.service)) + (S.boot.location ? ' · ' + h(S.boot.location.name) : '') + '</div>' : '') + '</div>';
    html += '<div class="row">';
    if (o.show_status) {
      html += '<span class="pill"><span class="dot ' + (e.vmix_ok ? 'ok' : 'bad') + '"></span>' + (e.vmix_ok ? 'vMix' : 'vMix offline') + '</span>';
    }
    html += '<div class="p-status ' + status.cls + '"><span class="d"></span>' + status.text + '</div></div></header>';
    if (o.show_set_switcher && sets().length > 1) {
      html += '<div class="p-sets"><div class="chips">';
      sets().forEach(function (s) {
        html += '<button type="button" class="chip" data-act="local-set" data-set="' + s.id + '" aria-pressed="' + (set && set.id === s.id) + '">' + h(s.guest || s.title) + '</button>';
      });
      html += '</div></div>';
    }
    var grid = renderGrid(itemsPulpit(), set ? 'Nothing in this set yet.' : 'Waiting for the production desk…');
    html += grid.html;
    html += renderTransport();
    html += '<footer class="foot">' + renderPager(grid.count);
    if (o.show_clear !== false) {
      html += '<button type="button" class="default-btn" data-act="clear"' + (e.asset_id || e.pending_id ? '' : ' disabled') + '>Clear screen</button>';
    }
    html += '</footer>';
    return html;
  }

  /* cue panel */

  function spriteStyle(a, t) {
    if (!a.filmstrip || !a.filmstrip_count) {
      return a.thumb ? 'background-image:url(' + a.thumb + ');background-size:cover;background-position:center;' : '';
    }
    var interval = a.filmstrip_interval_ms || 5000;
    var idx = Math.min(a.filmstrip_count - 1, Math.floor(t / interval));
    var cols = a.filmstrip_cols || a.filmstrip_count;
    var rows = Math.ceil(a.filmstrip_count / cols);
    var col = idx % cols;
    var row = Math.floor(idx / cols);
    var x = cols > 1 ? (col / (cols - 1)) * 100 : 0;
    var y = rows > 1 ? (row / (rows - 1)) * 100 : 0;
    return 'background-image:url(' + a.filmstrip + ');background-size:' + cols * 100 + '% ' + rows * 100 + '%;background-position:' + x + '% ' + y + '%;';
  }

  function renderCue() {
    if (!ui.cue) return '';
    var a = asset(ui.cue.id);
    if (!a) { ui.cue = null; return ''; }
    var dur = a.duration_ms || 1;
    var pos = ui.cue.pos;
    var pct = Math.round((pos / dur) * 100);
    var html = '<div class="overlay" data-act="cue-close-bg"><div class="sheet" role="dialog" aria-label="Cue video">';
    html += '<div class="sheet-head"><div><h2>Cue video</h2><div class="muted">' + h(a.name) + ' · ' + h(a.owner) + '</div></div>';
    html += '<button type="button" class="icon-btn" data-act="cue-close" aria-label="Close">' + ICON.close + '</button></div>';
    html += '<div class="cue-preview"><div class="frame" id="cue-frame" style="top:0;bottom:0;left:50%;transform:translateX(-50%);aspect-ratio:16/9;height:100%;' + spriteStyle(a, pos) + '"></div>';
    html += '<span class="off">Off-air · the projector is unchanged</span><span class="big" id="cue-time">' + fmt(pos) + '</span></div>';
    html += '<div class="cue-track" id="cue-track"><div class="bar"><span id="cue-bar" style="width:' + pct + '%"></span></div>';
    (a.markers || []).forEach(function (m) { html += '<span class="tick" style="left:calc(' + (m.t_ms / dur) * 100 + '% - 1px)"></span>'; });
    html += '<span class="knob" id="cue-knob" style="left:' + pct + '%"></span></div>';
    if (a.filmstrip_count) {
      html += '<div class="frames">';
      for (var i = 0; i < 12; i++) {
        var t = Math.floor((dur * i) / 12);
        var next = Math.floor((dur * (i + 1)) / 12);
        html += '<button type="button" data-act="cue-set" data-t="' + t + '" aria-pressed="' + (pos >= t && pos < next) + '" aria-label="Cue to ' + fmt(t) + '" style="' + spriteStyle(a, t) + '">' + fmt(t) + '</button>';
      }
      html += '</div>';
    }
    html += '<div class="row between"><div class="row">';
    [-30000, -10000, 10000, 30000].forEach(function (d) {
      html += '<button type="button" class="t-btn" data-act="cue-nudge" data-d="' + d + '">' + (d < 0 ? '−' : '+') + Math.abs(d / 1000) + 's</button>';
    });
    html += '</div>';
    html += '<div class="row"><span class="label">Markers</span>';
    (a.markers || []).forEach(function (m) {
      html += '<span class="mk-group"><button type="button" class="mk-btn" data-act="cue-set" data-t="' + m.t_ms + '">' + h(m.label) + ' · ' + fmt(m.t_ms) + '</button>';
      if (isBack() && m.id) html += '<button type="button" class="mk-del" data-act="marker-del" data-mid="' + m.id + '" aria-label="Remove marker ' + h(m.label) + '">' + ICON.close + '</button>';
      html += '</span>';
    });
    html += '<button type="button" class="t-btn" data-act="marker-cue">' + ICON.flag + ' Add marker here</button>';
    html += '</div>';
    html += '</div>';
    html += '<div class="row end"><button type="button" class="sec-btn" data-act="cue-close">Cancel</button>';
    html += '<button type="button" class="pri-btn" data-act="cue-start">' + ICON.play + ' Start here on ' + (eng().mix > 1 ? 'Mix ' + eng().mix : 'Main') + ' · <span id="cue-start-time">' + fmt(pos) + '</span></button></div>';
    html += '</div></div>';
    return html;
  }

  function updateCue() {
    var a = asset(ui.cue.id);
    var dur = a.duration_ms || 1;
    var pct = Math.round((ui.cue.pos / dur) * 100);
    var frame = document.getElementById('cue-frame');
    if (frame) frame.style.cssText = 'top:0;bottom:0;left:50%;transform:translateX(-50%);aspect-ratio:16/9;height:100%;' + spriteStyle(a, ui.cue.pos);
    ['cue-time', 'cue-start-time'].forEach(function (id) { var el = document.getElementById(id); if (el) el.textContent = fmt(ui.cue.pos); });
    var bar = document.getElementById('cue-bar'); if (bar) bar.style.width = pct + '%';
    var knob = document.getElementById('cue-knob'); if (knob) knob.style.left = pct + '%';
    document.querySelectorAll('[data-act=cue-set]').forEach(function (btn) { btn.setAttribute('aria-pressed', 'false'); });
  }

  /* naming a new marker */

  function renderMarkerSheet() {
    if (!ui.marker) return '';
    var a = asset(ui.marker.id);
    if (!a) { ui.marker = null; return ''; }
    return '<div class="overlay top"><form class="sheet small" id="marker-form" role="dialog" aria-label="New marker">' +
      '<div class="sheet-head"><div><h2>Marker at ' + fmt(ui.marker.t) + '</h2><div class="muted">' + h(a.name) + '</div></div></div>' +
      '<div class="field"><label class="label" for="marker-name">Name</label>' +
      '<input id="marker-name" class="text-input" maxlength="60" autocomplete="off" placeholder="e.g. Baptism (empty = Marker ' + fmt(ui.marker.t) + ')"></div>' +
      '<div class="row end"><button type="button" class="sec-btn" data-act="marker-cancel">Cancel</button>' +
      '<button type="submit" class="pri-btn">' + ICON.flag + ' Save marker</button></div></form></div>';
  }

  /* tablets drawer (back only) */

  function renderDrawer() {
    if (!ui.drawer || !isBack()) return '';
    var list = pulpits();
    var target = list.filter(function (p) { return p.id === ui.drawerScreen; })[0] || list[0];
    var html = '<aside class="drawer" aria-label="Tablets"><div class="drawer-head"><h2>Tablets</h2><button type="button" class="icon-btn" data-act="drawer-close" aria-label="Close">' + ICON.close + '</button></div><div class="drawer-body">';
    if (!target) {
      html += '<p class="muted">No pulpit tablet paired yet. Pair one from the backend or the tray companion on the vMix PC.</p>';
    } else {
      if (list.length > 1) {
        html += '<div class="seg">';
        list.forEach(function (p, i) {
          var dup = list.filter(function (q) { return q.name === p.name; }).length > 1;
          html += '<button type="button" data-act="drawer-screen" data-id="' + h(p.id) + '" aria-pressed="' + (p.id === target.id) + '">' + h(p.name) + (dup ? ' ' + (i + 1) : '') + '</button>';
        });
        html += '</div>';
      }
      html += '<div class="row"><span class="dot ' + (target.online ? 'ok' : 'warn') + '"></span><strong>' + h(target.name) + '</strong><span class="muted">' + (target.online ? 'connected' : 'not connected') + '</span></div>';
      html += '<div class="field"><label class="label" for="d-set">Showing on the pulpit</label><select id="d-set" data-change="screen-set" data-id="' + h(target.id) + '">';
      sets().forEach(function (s) { html += '<option value="' + s.id + '"' + (s.id === target.current_set_id ? ' selected' : '') + '>' + h(s.title) + ' (' + s.items.length + ')</option>'; });
      html += '</select><span class="hint">Changes appear on the pulpit immediately; the guest doesn’t need to find anything.</span></div>';
      html += '<div class="row"><button type="button" class="btn" data-act="screen-page" data-id="' + h(target.id) + '" data-page="0">Jump to page 1</button>';
      html += '<button type="button" class="btn" data-act="screen-page" data-id="' + h(target.id) + '" data-page="' + ui.page + '">Mirror my page</button></div>';
      var o = target.options || {};
      var checks = [
        ['show_clear', '"Clear screen" button'], ['show_title', 'Title of the set'], ['show_set_switcher', 'Switch between sets'],
        ['show_status', 'Connection status'], ['video_basic', 'Video: From start, Pause, markers'], ['video_seek', 'Video: free seeking (±10 s, cue)']
      ];
      html += '<fieldset class="box"><legend class="label">Visible on the pulpit</legend>';
      checks.forEach(function (c) {
        html += '<label class="check">' + c[1] + '<input type="checkbox" data-change="screen-opt" data-id="' + h(target.id) + '" data-opt="' + c[0] + '"' + (o[c[0]] ? ' checked' : '') + '></label>';
      });
      html += '</fieldset>';
      html += '<div class="field"><label class="label" for="d-theme">Pulpit theme</label><select id="d-theme" data-change="screen-theme" data-id="' + h(target.id) + '">';
      [['dark', 'Dark'], ['light', 'Light'], ['same', 'Same as back tablet']].forEach(function (t) {
        html += '<option value="' + t[0] + '"' + (target.theme === t[0] ? ' selected' : '') + '>' + t[1] + '</option>';
      });
      html += '</select></div>';
    }
    html += '<p class="hint">Projector mix, default input and pairing are in the companion: <a href="/companion">open settings</a>.</p>';
    html += '</div></aside>';
    return html;
  }

  function render() {
    if (!S.boot) return;
    var typing = document.getElementById('marker-name');
    if (ui.marker && typing && document.activeElement === typing) { updateProgress(); return; }
    S.renderKey = liveKey();
    document.body.className = 'theme-' + theme() + (isBack() ? ' theme-back' : ' theme-pulpit');
    var html = isBack() ? renderBack() : renderPulpit();
    html += renderCue();
    html += renderDrawer();
    html += renderMarkerSheet();
    if (ui.toast) html += '<div class="toast" role="alert">' + h(ui.toast) + '</div>';
    if (!S.wsOk) html += '<div class="offline">Reconnecting to the vMix PC…</div>';
    var scroll = {};
    app.querySelectorAll('.chips, .drawer-body').forEach(function (el, i) { scroll[i] = el.scrollLeft || el.scrollTop; });
    app.innerHTML = html;
    app.querySelectorAll('.chips, .drawer-body').forEach(function (el, i) {
      if (scroll[i]) { if (el.classList.contains('chips')) el.scrollLeft = scroll[i]; else el.scrollTop = scroll[i]; }
    });
    bindCueTrack();
    var form = document.getElementById('marker-form');
    if (form) {
      var input = document.getElementById('marker-name');
      input.focus();
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        var draft = ui.marker;
        ui.marker = null;
        post('/api/markers', { asset_id: draft.id, t_ms: draft.t, label: input.value }).then(function () { render(); });
        render();
      });
    }
  }

  function updateProgress() {
    var e = eng();
    var pct = progressPct();
    var bar = document.getElementById('prog-bar'); if (bar) bar.style.width = pct + '%';
    var tbar = document.getElementById('tile-bar'); if (tbar) tbar.style.width = pct + '%';
    var p = playing();
    var a = p ? asset(p.id) : null;
    var time = document.getElementById('prog-time');
    if (time && a) time.textContent = fmt(p.position_ms) + ' / ' + fmt(p.duration_ms || a.duration_ms);
    var note = document.getElementById('prog-note'); if (note) note.textContent = progressNote();
    var now = document.getElementById('now-label'); if (now) now.textContent = nowLabel().text;
  }

  /* ---------- actions ---------- */

  function tap(id) {
    var e = eng();
    var a = asset(id);
    if (!a) return;
    var au = audioState();
    if (au.asset_id === id || au.pending_id === id) { post('/api/audio/stop'); return; }
    if (e.asset_id === id || e.pending_id === id) { post('/api/clear'); return; }
    if (!a.ready) { showToast('Still downloading to the vMix PC'); return; }
    post('/api/take', { asset_id: id, start_ms: 0 });
  }

  function act(name, data) {
    var id = data.id ? parseInt(data.id, 10) : 0;
    var e = eng();
    switch (name) {
      case 'tap': tap(id); break;
      case 'resume': post('/api/take', { asset_id: id, start_ms: positions()[String(id)] || 0 }); break;
      case 'cue': ui.cue = { id: id, pos: positions()[String(id)] || 0 }; render(); break;
      case 'cue-close': ui.cue = null; render(); break;
      case 'cue-close-bg': break;
      case 'cue-set': ui.cue.pos = parseInt(data.t, 10); updateCue(); break;
      case 'cue-nudge': {
        var a = asset(ui.cue.id);
        ui.cue.pos = Math.max(0, Math.min((a.duration_ms || 0) - 500, ui.cue.pos + parseInt(data.d, 10)));
        updateCue();
        break;
      }
      case 'cue-start': post('/api/take', { asset_id: ui.cue.id, start_ms: ui.cue.pos }); ui.cue = null; render(); break;
      case 'marker-cue': ui.marker = { id: ui.cue.id, t: ui.cue.pos }; render(); break;
      case 'marker-live': {
        var pl = playing();
        if (pl) { ui.marker = { id: pl.id, t: pl.position_ms || 0 }; render(); }
        break;
      }
      case 'audio-stop': post('/api/audio/stop'); break;
      case 'marker-cancel': ui.marker = null; render(); break;
      case 'marker-del':
        if (confirm('Remove this marker?')) post('/api/markers/delete', { id: parseInt(data.mid, 10) });
        break;
      case 'clear': post('/api/clear'); break;
      case 'pause': post('/api/pause'); break;
      case 'play': post('/api/play'); break;
      case 'seek': post('/api/seek', { position_ms: parseInt(data.t, 10) }); break;
      case 'nudge': {
        var cur = playing();
        post('/api/seek', { position_ms: Math.max(0, ((cur && cur.position_ms) || 0) + parseInt(data.d, 10)) });
        break;
      }
      case 'page': ui.page += parseInt(data.d, 10); render(); break;
      case 'chip': ui.set = data.set === 'all' ? 'all' : parseInt(data.set, 10); ui.page = 0; render(); break;
      case 'local-set': ui.localSet = parseInt(data.set, 10); ui.page = 0; render(); break;
      case 'send-pulpit': if (ui.set !== 'all') post('/api/pulpit', { current_set_id: ui.set }); break;
      case 'theme': post('/api/screens/self', { theme: theme() === 'dark' ? 'light' : 'dark' }); break;
      case 'drawer': ui.drawer = true; render(); break;
      case 'drawer-close': ui.drawer = false; render(); break;
      case 'drawer-screen': ui.drawerScreen = data.id; render(); break;
      case 'screen-page': post('/api/screens/' + data.id + '/command', { action: 'goto_page', page: parseInt(data.page, 10) }); break;
    }
  }

  app.addEventListener('click', function (ev) {
    var el = ev.target.closest('[data-act]');
    if (!el || el.disabled) return;
    if (el.dataset.act === 'cue-close-bg' && ev.target !== el) return;
    if (el.dataset.act === 'cue-close-bg') { ui.cue = null; render(); return; }
    act(el.dataset.act, el.dataset);
  });

  app.addEventListener('change', function (ev) {
    var el = ev.target;
    var kind = el.dataset.change;
    if (!kind) return;
    if (kind === 'event') { post('/api/companion/settings', { service_id: parseInt(el.value, 10) || 0 }); return; }
    var screen = S.screens.filter(function (s) { return s.id === el.dataset.id; })[0];
    if (!screen) return;
    if (kind === 'screen-set') post('/api/screens/' + screen.id, { current_set_id: parseInt(el.value, 10) });
    if (kind === 'screen-theme') post('/api/screens/' + screen.id, { theme: el.value });
    if (kind === 'screen-opt') {
      var options = Object.assign({}, screen.options);
      options[el.dataset.opt] = el.checked;
      post('/api/screens/' + screen.id, { options: options });
    }
  });

  function bindCueTrack() {
    var track = document.getElementById('cue-track');
    if (!track || !ui.cue) return;
    function setFrom(ev) {
      var rect = track.getBoundingClientRect();
      var frac = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
      var a = asset(ui.cue.id);
      ui.cue.pos = Math.round(frac * Math.max(0, (a.duration_ms || 0) - 500));
      updateCue();
    }
    track.addEventListener('pointerdown', function (ev) {
      track.setPointerCapture(ev.pointerId);
      setFrom(ev);
      track.onpointermove = setFrom;
    });
    track.addEventListener('pointerup', function () { track.onpointermove = null; });
  }

  /* swipe between pages */
  var touchX = null;
  app.addEventListener('touchstart', function (ev) {
    if (ev.target.closest('.grid') && ev.touches.length === 1) touchX = ev.touches[0].clientX;
  }, { passive: true });
  app.addEventListener('touchend', function (ev) {
    if (touchX === null) return;
    var dx = ev.changedTouches[0].clientX - touchX;
    touchX = null;
    if (Math.abs(dx) < 80) return;
    var list = isBack() ? itemsBack() : itemsPulpit();
    var count = Math.max(1, Math.ceil(list.length / PER_PAGE));
    var next = ui.page + (dx < 0 ? 1 : -1);
    if (next >= 0 && next < count) { ui.page = next; render(); }
  }, { passive: true });

  // Keep the screen awake where the browser allows it.
  if (navigator.wakeLock) {
    var requestWake = function () { navigator.wakeLock.request('screen').catch(function () {}); };
    document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible') requestWake(); });
    requestWake();
  }

  connect();
})();
