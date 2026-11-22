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
  var swiped = 0;
  var scrolling = 0;
  var scrollTimer = null;
  var firstIndex = 0;
  var scrollToIndex = null;
  var lastKey = '';

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

  function layout() {
    var l = LAYOUTS[PER_PAGE][window.innerWidth >= window.innerHeight ? 0 : 1];
    var rows = l[1];
    // fewer rows on a low screen (a phone held sideways)
    var room = window.innerHeight - 330;
    while (rows > 1 && room / rows < 120) rows--;
    return { cols: window.innerWidth < 520 ? Math.min(l[0], 2) : l[0], rows: rows };
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

  function metrics(grid) {
    var gap = parseFloat(getComputedStyle(grid).columnGap) || 0;
    var first = grid.querySelector('.tile');
    var colW = (first ? first.getBoundingClientRect().width : grid.clientWidth) + gap;
    var cols = parseInt(grid.style.getPropertyValue('--cols'), 10) || 1;
    var max = grid.scrollWidth - grid.clientWidth;
    var step = colW * cols;
    return { colW: colW, step: step, max: max, count: Math.max(1, Math.round(max / step) + 1) };
  }
  function goToPage(p) {
    var grid = root.querySelector('.grid');
    if (!grid) return;
    var m = metrics(grid);
    p = Math.max(0, Math.min(m.count - 1, p));
    grid.scrollTo({ left: Math.min(m.max, p * m.step), behavior: 'smooth' });
  }
  function syncPager() {
    var grid = root.querySelector('.grid');
    if (!grid || !grid.querySelector('.tile')) return;
    var m = metrics(grid);
    page = grid.scrollLeft >= m.max - 2 ? m.count - 1 : Math.min(m.count - 1, Math.round(grid.scrollLeft / m.step));
    firstIndex = Math.round(grid.scrollLeft / m.colW) * (parseInt(grid.style.getPropertyValue('--rows'), 10) || 1);
    root.querySelectorAll('.pdot').forEach(function (d, i) { if (i === page) d.setAttribute('aria-current', 'page'); else d.removeAttribute('aria-current'); });
    var slider = root.querySelector('.pslider');
    if (slider && document.activeElement !== slider) { slider.value = page + 1; slider.nextElementSibling.textContent = (page + 1) + ' / ' + m.count; }
    var prev = root.querySelector('[data-act=page][data-d="-1"]'), next = root.querySelector('[data-act=page][data-d="1"]');
    if (prev) prev.disabled = grid.scrollLeft <= 2;
    if (next) next.disabled = grid.scrollLeft >= m.max - 2;
  }


  function render() {
    if (scrolling) return; // the next poll redraws once the strip has settled
    if (!state) { root.innerHTML = '<div class="banner">Loading…</div>'; return; }
    var cur = current();
    var list = items();
    var lay = layout();
    var pages = Math.max(1, Math.ceil(Math.ceil(list.length / lay.rows) / lay.cols));
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

    html += '<main class="grid' + (list.length ? '' : ' empty-grid') + '" style="--cols:' + lay.cols + ';--rows:' + lay.rows + '">';
    var slice = list;
    if (!slice.length) html += '<div class="empty">Your set is empty. Add files on your page first.</div>';
    slice.forEach(function (a, i) {
      var n = i + 1;
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
    var old = root.querySelector('.grid');
    var keepLeft = old ? old.scrollLeft : 0;
    var key = list.map(function (a) { return a.id; }).join(',') + '|' + PER_PAGE;
    root.innerHTML = html;
    var grid = root.querySelector('.grid');
    if (grid) {
      if (scrollToIndex != null) {
        grid.scrollLeft = Math.floor(scrollToIndex / lay.rows) * metrics(grid).colW;
        scrollToIndex = null;
      } else if (key === lastKey) {
        grid.scrollLeft = keepLeft;
      }
      grid.addEventListener('scroll', function () {
        scrolling = Date.now();
        clearTimeout(scrollTimer);
        scrollTimer = setTimeout(function () { scrolling = 0; syncPager(); }, 200);
        syncPager();
      }, { passive: true });
      syncPager();
    }
    lastKey = key;
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
    if (el.dataset.actChange === 'page') goToPage(parseInt(el.value, 10) - 1);
    if (el.dataset.actChange === 'per-page') {
      PER_PAGE = parseInt(el.value, 10) || 6;
      try { localStorage.setItem('vitrine_per_page', String(PER_PAGE)); } catch (e) { /* private mode */ }
      scrollToIndex = firstIndex; // keep the first visible file in view
      render();
    }
  });

  /* Mouse: drag the strip sideways like a finger (touch scrolls natively). */
  var drag = null;
  root.addEventListener('pointerdown', function (ev) {
    if (ev.pointerType !== 'mouse' || ev.button !== 0) return;
    var grid = ev.target.closest('.grid');
    if (grid) drag = { x: ev.clientX, left: grid.scrollLeft, grid: grid, moved: false };
  });
  window.addEventListener('pointermove', function (ev) {
    if (!drag) return;
    var dx = ev.clientX - drag.x;
    if (!drag.moved && Math.abs(dx) < 6) return;
    if (!drag.moved) { drag.moved = true; drag.grid.classList.add('dragging'); }
    drag.grid.scrollLeft = drag.left - dx;
  });
  window.addEventListener('pointerup', function () {
    if (!drag) return;
    var d = drag;
    drag = null;
    if (!d.moved) return;
    swiped = Date.now();
    d.grid.classList.remove('dragging');
    var m = metrics(d.grid);
    d.grid.scrollTo({ left: Math.round(d.grid.scrollLeft / m.colW) * m.colW, behavior: 'smooth' });
  });
  root.addEventListener('click', function (ev) {
    if (Date.now() - swiped < 300 && ev.target.closest('.grid')) { ev.stopPropagation(); ev.preventDefault(); }
  }, true);
  window.addEventListener('resize', function () { scrollToIndex = firstIndex; render(); });

  root.addEventListener('click', function (ev) {
    var el = ev.target.closest('[data-act]');
    if (!el || el.disabled) return;
    var act = el.dataset.act;
    var cur = current();
    switch (act) {
      case 'tap': tap(parseInt(el.dataset.id, 10)); break;
      case 'request': send('request_access'); break;
      case 'cancel': send('cancel_request'); break;
      case 'page': goToPage(page + parseInt(el.dataset.d, 10)); break;
      case 'page-to': goToPage(parseInt(el.dataset.page, 10)); break;
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
