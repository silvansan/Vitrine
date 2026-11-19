/* Companion page: settings for this vMix PC. Opens from the tray icon. */
(function () {
  'use strict';

  var data = null;
  var editing = false;

  function $(id) { return document.getElementById(id); }
  function h(text) {
    return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function bytes(n) {
    if (!n) return '0 MB';
    return (n / 1048576).toFixed(n > 1048576 * 100 ? 0 : 1) + ' MB';
  }
  function post(path, body) {
    return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) alert(d.error || 'Failed'); return d; }); });
  }

  function load() {
    return fetch('/api/companion/state', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) {
      data = d;
      render();
    });
  }

  function option(value, label, selected) {
    return '<option value="' + h(value) + '"' + (selected ? ' selected' : '') + '>' + h(label) + '</option>';
  }

  function render() {
    var s = data.settings;
    var st = data.status;
    var e = st.engine;
    $('agent-line').textContent = 'Version ' + data.agent.version + ' · tablets connect to http://' + data.agent.lan_ip + ':' + data.agent.port + ' · media in ' + data.agent.media_dir;

    var rows = [
      [st.server_online ? 'ok' : 'warn', 'Server', st.server_online ? 'Connected (' + data.agent.server_url + ')' : 'Not reachable — tablets keep working with the files already here'],
      [e.vmix_ok ? 'ok' : 'bad', 'vMix', e.vmix_ok ? 'Connected' + (e.vmix_version ? ' (v' + e.vmix_version + ')' : '') : 'Not reachable at ' + data.agent.vmix_url + ' — is vMix running with the Web Controller enabled?'],
      [e.default_ok ? 'ok' : 'warn', 'Default input', e.default_ok ? s.default_input_title : 'Not chosen yet'],
      [e.inputs_ok ? 'ok' : 'warn', 'Guest inputs', e.inputs_ok ? s.input_a + ' and ' + s.input_b + ' found' : (e.inputs_error || 'missing')],
      [st.sync.done === st.sync.total ? 'ok' : 'warn', 'Files', st.sync.done + ' of ' + st.sync.total + ' on this PC' + (st.sync.pending_bytes > 0 ? ' · ' + bytes(st.sync.pending_bytes) + ' to go' : '') + (st.sync.current ? ' · now: ' + st.sync.current : '') + (st.sync.last_error ? ' · last error: ' + st.sync.last_error : '')],
      [e.asset_id ? 'bad' : 'ok', 'On the projector', e.asset_id ? 'Guest media is live' : (e.mix_active || '—')]
    ];
    $('status').innerHTML = rows.map(function (r) {
      return '<span class="dot ' + r[0] + '"></span><div><strong>' + h(r[1]) + ':</strong> ' + h(r[2]) + '</div>';
    }).join('');

    if (editing) return;
    $('service').innerHTML = (data.services || []).map(function (sv) {
      return option(sv.id, sv.name + ' · ' + sv.date, data.service && data.service.id === sv.id);
    }).join('') || option(0, 'No active service on the server', true);

    $('mix').innerHTML = data.mixes.map(function (m) { return option(m, m === 1 ? 'Main output' : 'Mix ' + m, s.mix === m); }).join('');
    var inputs = data.inputs || [];
    var defaultHtml = option('', '— choose —', !s.default_input_key);
    var found = false;
    inputs.forEach(function (input) {
      var selected = input.key === s.default_input_key;
      if (selected) found = true;
      defaultHtml += option(input.key, input.number + ' · ' + input.name, selected);
    });
    if (!found && s.default_input_key) defaultHtml += option(s.default_input_key, s.default_input_title + ' (not in vMix right now)', true);
    $('default').innerHTML = defaultHtml;
    $('transition').innerHTML = data.transitions.map(function (t) { return option(t, t, s.transition === t); }).join('');
    $('transition_ms').value = s.transition_ms;
    $('video_end_return').checked = !!s.video_end_return;
    $('seek_mode').value = s.seek_mode;
    $('input_a').value = s.input_a;
    $('input_b').value = s.input_b;

    var screens = data.screens || [];
    $('tablets').innerHTML = screens.length ? screens.map(function (sc) {
      return '<tr><td><span class="dot ' + (sc.online ? 'ok' : '') + '"></span> <strong>' + h(sc.name) + '</strong></td><td>' + (sc.role === 'back' ? 'Back' : 'Pulpit') +
        '</td><td class="muted">' + (sc.online ? 'connected' : 'last seen ' + new Date(sc.last_seen).toLocaleString()) +
        '</td><td><button type="button" class="btn" data-unpair="' + h(sc.id) + '">Unpair</button></td></tr>';
    }).join('') : '<tr><td class="muted">No tablets paired yet.</td></tr>';
  }

  document.addEventListener('focusin', function (e) { if (e.target.matches('select, input')) editing = true; });
  document.addEventListener('focusout', function () { editing = false; });

  document.addEventListener('change', function (e) {
    var el = e.target;
    if (el.id === 'default') {
      var input = (data.inputs || []).filter(function (i) { return i.key === el.value; })[0];
      post('/api/companion/settings', { default_input_key: el.value, default_input_title: input ? input.name : '' }).then(load);
      return;
    }
    var key = el.dataset.setting;
    if (!key) return;
    var value = el.type === 'checkbox' ? el.checked : el.value;
    if (el.dataset.type === 'int') value = parseInt(value, 10) || 0;
    var body = {};
    body[key] = value;
    post('/api/companion/settings', body).then(load);
  });

  document.addEventListener('click', function (e) {
    var unpair = e.target.closest('[data-unpair]');
    if (unpair && confirm('Unpair this tablet? It will need a new QR code.')) {
      post('/api/companion/screens/' + unpair.dataset.unpair + '/delete').then(load);
    }
  });

  $('btn-sync').addEventListener('click', function () { post('/api/companion/sync').then(load); });
  $('btn-test').addEventListener('click', function () { post('/api/companion/test-default').then(load); });
  $('btn-pair').addEventListener('click', function () {
    post('/api/companion/pair', { role: $('pair-role').value, name: $('pair-name').value }).then(function (res) {
      if (!res || !res.url) return;
      var qr = qrcode(0, 'M');
      qr.addData(res.url);
      qr.make();
      $('qr').innerHTML = qr.createSvgTag({ cellSize: 6, margin: 2, scalable: false });
      $('qr-url').textContent = res.url;
      $('qr-source').textContent = res.source === 'agent'
        ? 'The server was not reachable, so this code was made by the agent itself.'
        : 'Signed by the Vitrine server.';
      $('qr-box').hidden = false;
    });
  });

  var proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
  function connect() {
    var ws = new WebSocket(proto + location.host + '/ws');
    ws.onmessage = function (e) {
      var msg = JSON.parse(e.data);
      if (msg.type === 'state' && data) { data.status = msg; render(); }
      else load();
    };
    ws.onclose = function () { setTimeout(connect, 2000); };
  }

  load().then(function () {
    connect();
    if (location.hash === '#pair') $('pair').scrollIntoView();
  });
  setInterval(function () { if (!editing) load(); }, 15000);
})();
