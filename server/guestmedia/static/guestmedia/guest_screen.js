/* A guest's own tablet view.
 *
 * Shows their set like the pulpit tablet. Until the production desk gives them
 * control it is a practice view: taps are simulated and nothing reaches vMix.
 * The red badge asks the back tablet for access; green means their taps go live
 * (relayed by the server to the vMix PC, which checks the grant again).
 */
(function () {
  'use strict';
  var root = document.getElementById('app');
  var token = root.dataset.token;
  var q = '?e=' + encodeURIComponent(root.dataset.event);
  var api = '/g/' + token + '/api/';
  var PER_PAGE = 6;

  var state = null;   // /api/state: set, guest, service
  var live = null;    // /api/live: control, requested, live, audio, …
  var page = 0;
  var sim = null;     // practice: { asset_id, kind, start, offset, paused, duration_ms }
  var toastTimer = null;
  var busy = false;

  function h(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  function fmt(ms) {
    var t = Math.max(0, Math.floor((ms || 0) / 1000));
    var m = Math.floor(t / 60), s = t % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }
  function toast(text) {
    clearTimeout(toastTimer);
    var el = document.querySelector('.toast');
    if (!el) { el = document.createElement('div'); el.className = 'toast'; el.setAttribute('role', 'alert'); document.body.appendChild(el); }
    el.textContent = text;
    toastTimer = setTimeout(function () { el.remove(); }, 3500);
  }
  function get(path) {
    return fetch(api + path + q, { cache: 'no-store' }).then(function (r) {
      if (r.status === 401 || r.status === 403) { location.href = '/g/' + token + '/' + q; throw new Error('pin'); }
      return r.json();
    });
  }
  function post(path, body) {
    return fetch(api + path + q, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json(); });
  }

  function items() { return (state && state.set) || []; }
  function control() { return !!(live && live.control); }

  // What is on screen from this guest's point of view: live (with control) or simulated.
  function current() {
    if (control() || !sim) {
      var l = live && (live.live || live.audio);
      if (!l) return null;
      return { asset_id: l.asset_id, pending_id: l.pending_id, kind: l.kind, position_ms: l.position_ms, duration_ms: l.duration_ms, paused: l.paused, audio: !live.live };
    }
    var pos = sim.paused ? sim.offset : sim.offset + (Date.now() - sim.start);
    if (sim.duration_ms && pos >= sim.duration_ms) { sim = null; return null; }
    return { asset_id: sim.asset_id, kind: sim.kind, position_ms: pos, duration_ms: sim.duration_ms, paused: sim.paused, simulated: true };
  }

  function badge() {
    if (!live || !live.online) return '<span class="access grey"><span class="d"></span>vMix PC offline · practice</span>';
    if (!live.here) return '<span class="access grey"><span class="d"></span>Not your event right now · practice</span>';
    if (live.control) return '<span class="access green"><span class="d"></span>You control the screen</span>';
    if (live.requested) return '<button type="button" class="access amber" data-act="cancel"><span class="d"></span>Waiting for the desk… (cancel)</button>';
    return '<button type="button" class="access red" data-act="request"><span class="d"></span>Ask for access</button>';
  }

  function render() {
    if (!state) { root.innerHTML = '<div class="banner">Loading…</div>'; return; }
    var cur = current();
    var list = items();
    var pages = Math.max(1, Math.ceil(list.length / PER_PAGE));
    if (page >= pages) page = pages - 1;
    var html = '<header class="head"><div class="who"><div class="name">' + h(state.guest.name) + '</div><div class="sub">' +
      h(state.service.name) + ' · ' + h(state.service.when || '') + '</div></div>' +
      '<a class="back" href="' + h(root.dataset.back) + '">← My set</a>' + badge() + '</header>';

    if (control()) {
      html += '<div class="banner ok">Your taps go to the projector. Only your own files; the production desk can take over at any time.</div>';
    } else {
      html += '<div class="banner">Practice: taps don\'t reach the projector. ' +
        (live && live.online && live.here ? 'Tap <strong>Ask for access</strong> when it\'s your turn; the production desk turns it green.' : 'You can try everything here.') + '</div>';
    }

    var status = 'Screen shows the banner', cls = '';
    if (cur && cur.pending_id) { status = 'Sending to the screen…'; cls = 'pend'; }
    else if (cur && cur.asset_id) {
      status = (cur.simulated ? 'Practice: ' : '') + (cur.audio ? 'Your audio is playing' : cur.kind === 'video' ? 'Your video is on screen' : 'Your picture is on screen');
      cls = 'live';
    } else if (control() && live.other_on_screen) { status = 'Something else is on screen'; }
    html += '<div class="status ' + cls + '"><span class="d"></span>' + status + '</div>';

    html += '<main class="grid">';
    var slice = list.slice(page * PER_PAGE, page * PER_PAGE + PER_PAGE);
    if (!slice.length) html += '<div class="empty">Your set is empty. Add files on your page first.</div>';
    slice.forEach(function (a, i) {
      var n = page * PER_PAGE + i + 1;
      var isLive = cur && cur.asset_id === a.id;
      var isPend = cur && cur.pending_id === a.id;
      var label = a.kind === 'video' ? 'Video · ' + fmt(a.duration_ms) : a.kind === 'audio' ? 'Audio · ' + fmt(a.duration_ms) : 'Photo';
      html += '<button type="button" class="tile' + (isLive ? ' live' : '') + (isPend ? ' pend' : '') + '" data-act="tap" data-id="' + a.id + '">' +
        (a.thumb ? '<img src="' + h(a.thumb) + '" alt="">' : '<span class="ph">' + h(a.kind) + '</span>') +
        '<span class="num">' + n + '</span>' + '<span class="meta">' + h(label) + '</span>' +
        (isLive ? '<span class="badge">' + (cur.simulated ? 'PRACTICE' : 'ON SCREEN') + '</span>' : '') +
        (isPend ? '<span class="badge">SENDING…</span>' : '') +
        (isLive && cur.duration_ms ? '<span class="bar" style="width:' + Math.min(100, 100 * cur.position_ms / cur.duration_ms) + '%"></span>' : '') +
        '</button>';
    });
    html += '</main>';

    if (cur && cur.asset_id && (cur.kind === 'video' || cur.kind === 'audio')) {
      var a2 = list.filter(function (x) { return x.id === cur.asset_id; })[0] || {};
      html += '<div class="transport">' +
        '<button type="button" data-act="seek" data-to="0">From start</button>' +
        '<button type="button" data-act="skip" data-d="-10000">−10s</button>' +
        '<button type="button" data-act="' + (cur.paused ? 'play' : 'pause') + '">' + (cur.paused ? 'Play' : 'Pause') + '</button>' +
        '<button type="button" data-act="skip" data-d="10000">+10s</button>';
      (a2.markers || []).slice().sort(function (x, y) { return x.t_ms - y.t_ms; }).forEach(function (m) {
        html += '<button type="button" data-act="seek" data-to="' + m.t_ms + '">' + h(m.label || fmt(m.t_ms)) + ' · ' + fmt(m.t_ms) + '</button>';
      });
      html += '<div class="prog"><div class="track"><i style="width:' + (cur.duration_ms ? Math.min(100, 100 * cur.position_ms / cur.duration_ms) : 0) + '%"></i></div>' +
        fmt(Math.max(0, (cur.duration_ms || 0) - (cur.position_ms || 0))) + ' left</div></div>';
    }

    html += '<footer class="foot"><div class="pager"><button type="button" data-act="page" data-d="-1"' + (page > 0 ? '' : ' disabled') + ' aria-label="Previous page">‹</button>' +
      '<span>' + (page + 1) + ' / ' + pages + '</span><button type="button" data-act="page" data-d="1"' + (page < pages - 1 ? '' : ' disabled') + ' aria-label="Next page">›</button></div>' +
      '<button type="button" class="clear" data-act="clear"' + (cur && (cur.asset_id || cur.pending_id) ? '' : ' disabled') + '>Back to the banner</button></footer>';
    root.innerHTML = html;
  }

  function send(action, body) {
    if (busy) return;
    busy = true;
    post('live/' + action, body).then(function (res) {
      busy = false;
      if (res.live) live = res.live;
      if (!res.ok && res.error) toast(res.error);
      render();
    }).catch(function () { busy = false; toast('No connection.'); });
  }

  function tap(id) {
    var a = items().filter(function (x) { return x.id === id; })[0];
    if (!a) return;
    var cur = current();
    if (control()) {
      if (cur && (cur.asset_id === id || cur.pending_id === id)) send(cur.audio ? 'stop_audio' : 'clear');
      else send('take', { asset_id: id, start_ms: 0 });
      return;
    }
    if (cur && cur.asset_id === id) { sim = null; }
    else { sim = { asset_id: id, kind: a.kind, start: Date.now(), offset: 0, paused: false, duration_ms: a.duration_ms || 0 }; }
    render();
  }

  function seekTo(ms) {
    var cur = current();
    if (!cur) return;
    ms = Math.max(0, Math.min(ms, Math.max(0, (cur.duration_ms || 0) - 500)));
    if (control()) { send('seek', { position_ms: ms }); return; }
    if (sim) { sim.offset = ms; sim.start = Date.now(); render(); }
  }

  root.addEventListener('click', function (ev) {
    var el = ev.target.closest('[data-act]');
    if (!el || el.disabled) return;
    var act = el.dataset.act;
    var cur = current();
    switch (act) {
      case 'tap': tap(parseInt(el.dataset.id, 10)); break;
      case 'request': send('request_access'); break;
      case 'cancel': send('cancel_request'); break;
      case 'page': page += parseInt(el.dataset.d, 10); render(); break;
      case 'clear':
        if (control()) send(cur && cur.audio ? 'stop_audio' : 'clear');
        else { sim = null; render(); }
        break;
      case 'pause':
      case 'play':
        if (control()) send(act);
        else if (sim) {
          if (act === 'pause') { sim.offset = sim.offset + (Date.now() - sim.start); sim.paused = true; }
          else { sim.start = Date.now(); sim.paused = false; }
          render();
        }
        break;
      case 'seek': seekTo(parseInt(el.dataset.to, 10)); break;
      case 'skip': if (cur) seekTo(cur.position_ms + parseInt(el.dataset.d, 10)); break;
    }
  });

  function poll() {
    get('live').then(function (data) {
      var wasControl = control();
      live = data;
      if (!wasControl && live.control) { sim = null; toast('You control the screen now.'); }
      if (wasControl && !live.control) toast('The production desk took control back.');
      render();
    }).catch(function () {}).then(function () { setTimeout(poll, 1000); });
  }

  get('state').then(function (data) { state = data; render(); poll(); });
  setInterval(function () { if (sim && !sim.paused) render(); }, 500);
})();
