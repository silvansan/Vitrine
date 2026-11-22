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
  // Files per page, remembered on this device.
  var PAGE_SIZES = [4, 6, 8, 9, 12, 16];
  var LAYOUTS = { 4: [[2, 2], [2, 2]], 6: [[3, 2], [2, 3]], 8: [[4, 2], [2, 4]], 9: [[3, 3], [3, 3]], 12: [[4, 3], [3, 4]], 16: [[4, 4], [4, 4]] };
  var PER_PAGE = 6;
  try { PER_PAGE = parseInt(localStorage.getItem('vitrine_per_page'), 10) || 6; } catch (e) { /* private mode */ }
  if (!LAYOUTS[PER_PAGE]) PER_PAGE = 6;
  var swipe = null;
  var swiped = 0;

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

  function gridStyle() {
    if (PER_PAGE === 6 || window.innerWidth < 700) return '';
    var l = LAYOUTS[PER_PAGE][window.innerWidth >= window.innerHeight ? 0 : 1];
    return ' style="grid-template-columns:repeat(' + l[0] + ',minmax(0,1fr));grid-template-rows:repeat(' + l[1] + ',minmax(0,1fr));grid-auto-rows:minmax(0,1fr)"';
  }

  function pager(pages) {
    var html = '<div class="pager">';
    if (pages > 1) {
      html += '<button type="button" data-act="page" data-d="-1"' + (page > 0 ? '' : ' disabled') + ' aria-label="Previous page">‹</button>';
      if (pages <= 8) {
        html += '<span class="dots">';
        for (var i = 0; i < pages; i++) html += '<button type="button" class="pdot" data-act="page-to" data-page="' + i + '" aria-label="Page ' + (i + 1) + ' of ' + pages + '"' + (i === page ? ' aria-current="page"' : '') + '></button>';
        html += '</span>';
      } else {
        html += '<input type="range" class="pslider" min="1" max="' + pages + '" value="' + (page + 1) + '" data-act-change="page" aria-label="Page"><span>' + (page + 1) + ' / ' + pages + '</span>';
      }
      html += '<button type="button" data-act="page" data-d="1"' + (page < pages - 1 ? '' : ' disabled') + ' aria-label="Next page">›</button>';
    }
    html += '<select class="per-page" data-act-change="per-page" aria-label="Files per page">';
    PAGE_SIZES.forEach(function (n) { html += '<option value="' + n + '"' + (n === PER_PAGE ? ' selected' : '') + '>' + n + ' per page</option>'; });
    return html + '</select></div>';
  }

  function goToPage(next, dir) {
    var pages = Math.max(1, Math.ceil(items().length / PER_PAGE));
    if (next < 0 || next >= pages || next === page) return false;
    page = next;
    render();
    var grid = root.querySelector('.grid');
    if (grid && dir) {
      grid.style.transition = 'none';
      grid.style.transform = 'translateX(' + dir * 40 + 'px)';
      grid.style.opacity = '0.4';
      requestAnimationFrame(function () {
        grid.style.transition = 'transform .22s ease-out, opacity .22s ease-out';
        grid.style.transform = '';
        grid.style.opacity = '';
      });
    }
    return true;
  }

  function render() {
    if (swipe && swipe.active) return; // the next poll redraws after the swipe
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

    html += '<main class="grid"' + gridStyle() + '>';
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

    html += '<footer class="foot">' + pager(pages) +
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

  root.addEventListener('change', function (ev) {
    var el = ev.target;
    if (el.dataset.actChange === 'page') goToPage(parseInt(el.value, 10) - 1, 0);
    if (el.dataset.actChange === 'per-page') {
      var first = page * PER_PAGE;
      PER_PAGE = parseInt(el.value, 10) || 6;
      try { localStorage.setItem('vitrine_per_page', String(PER_PAGE)); } catch (e) { /* private mode */ }
      page = Math.floor(first / PER_PAGE);
      render();
    }
  });

  /* Swipe between pages: the grid follows the finger. */
  root.addEventListener('pointerdown', function (ev) {
    var grid = ev.target.closest('.grid');
    if (!grid || (ev.pointerType === 'mouse' && ev.button !== 0)) return;
    swipe = { x: ev.clientX, y: ev.clientY, t: Date.now(), grid: grid, active: false, id: ev.pointerId, dx: 0 };
  });
  root.addEventListener('pointermove', function (ev) {
    if (!swipe || ev.pointerId !== swipe.id) return;
    var dx = ev.clientX - swipe.x, dy = ev.clientY - swipe.y;
    if (!swipe.active) {
      if (Math.abs(dx) < 12 || Math.abs(dx) < Math.abs(dy) * 1.3) { if (Math.abs(dy) > 16) swipe = null; return; }
      swipe.active = true;
      swipe.grid.style.transition = 'none';
    }
    var pages = Math.max(1, Math.ceil(items().length / PER_PAGE));
    var atEdge = (dx > 0 && page === 0) || (dx < 0 && page >= pages - 1);
    swipe.dx = atEdge ? dx * 0.25 : dx;
    swipe.grid.style.transform = 'translateX(' + swipe.dx + 'px)';
  });
  function endSwipe(ev) {
    if (!swipe || (ev && ev.pointerId !== swipe.id)) return;
    var s = swipe;
    swipe = null;
    if (!s.active) return;
    swiped = Date.now();
    var fast = Math.abs(s.dx) / Math.max(1, Date.now() - s.t) > 0.5;
    var far = Math.abs(s.dx) > s.grid.clientWidth * 0.18;
    if ((fast || far) && Math.abs(s.dx) > 30) {
      var dir = s.dx < 0 ? 1 : -1;
      s.grid.style.transition = 'transform .15s ease-in';
      s.grid.style.transform = 'translateX(' + (-dir) * s.grid.clientWidth * 0.5 + 'px)';
      setTimeout(function () { if (!goToPage(page + dir, dir)) render(); }, 120);
    } else {
      s.grid.style.transition = 'transform .2s ease-out';
      s.grid.style.transform = '';
    }
  }
  root.addEventListener('pointerup', endSwipe);
  root.addEventListener('pointercancel', endSwipe);
  root.addEventListener('click', function (ev) {
    if (Date.now() - swiped < 400 && ev.target.closest('.grid')) { ev.stopPropagation(); ev.preventDefault(); }
  }, true);
  window.addEventListener('resize', function () { if (PER_PAGE !== 6) render(); });

  root.addEventListener('click', function (ev) {
    var el = ev.target.closest('[data-act]');
    if (!el || el.disabled) return;
    var act = el.dataset.act;
    var cur = current();
    switch (act) {
      case 'tap': tap(parseInt(el.dataset.id, 10)); break;
      case 'request': send('request_access'); break;
      case 'cancel': send('cancel_request'); break;
      case 'page': { var d = parseInt(el.dataset.d, 10); goToPage(page + d, d); } break;
      case 'page-to': { var to = parseInt(el.dataset.page, 10); goToPage(to, to > page ? 1 : -1); } break;
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
  // Keep the screen on while this view is open (https, so the Wake Lock API works).
  if (navigator.wakeLock) {
    var wake = function () { navigator.wakeLock.request('screen').catch(function () {}); };
    document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible') wake(); });
    wake();
  }
})();
