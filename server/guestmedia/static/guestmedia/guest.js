/* Guest page: My set / Upload / Library. Plain JS, no build step. */
(function () {
  'use strict';

  var root = document.getElementById('app');
  var token = root.dataset.token;
  var tusEndpoint = root.dataset.tus;
  var maxBytes = parseInt(root.dataset.maxMb, 10) * 1024 * 1024;
  var api = '/g/' + token + '/api/';

  var state = { set: [], library: [] };
  var ui = { tab: 'mine', filter: 'all', selected: [], openExtras: {} };
  var pollTimer = null;

  function $(sel) { return document.querySelector(sel); }
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      if (key === 'text') node.textContent = attrs[key];
      else if (key === 'html') node.innerHTML = attrs[key];
      else if (key.indexOf('on') === 0) node.addEventListener(key.slice(2), attrs[key]);
      else node.setAttribute(key, attrs[key]);
    });
    (children || []).forEach(function (child) { if (child) node.appendChild(child); });
    return node;
  }
  function fmt(ms) {
    var total = Math.floor((ms || 0) / 1000);
    var s = total % 60;
    return Math.floor(total / 60) + ':' + (s < 10 ? '0' + s : s);
  }
  function parseTime(text) {
    var parts = String(text || '').trim().split(':');
    var seconds = 0;
    for (var i = 0; i < parts.length; i++) {
      var n = parseFloat(parts[i]);
      if (isNaN(n)) return null;
      seconds = seconds * 60 + n;
    }
    return Math.round(seconds * 1000);
  }
  var ICON = {
    up: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 15l6-6 6 6"/></svg>',
    down: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>',
    remove: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    more: '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>',
    check: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5 9-10"/></svg>'
  };

  function post(path, body) {
    return fetch(api + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); }).then(setState);
  }
  function load() {
    return fetch(api + 'state').then(function (r) { return r.json(); }).then(setState);
  }
  function setState(data) {
    state = data;
    render();
    var busy = state.set.concat(state.library).some(function (a) { return a.status === 'processing'; });
    clearTimeout(pollTimer);
    if (busy) pollTimer = setTimeout(load, 5000);
  }

  function statusBadge(asset) {
    if (asset.status === 'processing') return el('span', { class: 'badge', text: 'Processing…' });
    if (asset.status === 'failed') return el('span', { class: 'badge bad', text: 'Could not be processed' });
    if (asset.approval === 'pending') return el('span', { class: 'badge', text: 'Waiting for approval' });
    if (asset.approval === 'rejected') return el('span', { class: 'badge bad', text: 'Not approved' });
    return null;
  }

  function renderSet() {
    var list = $('#set-list');
    list.innerHTML = '';
    $('#count').textContent = state.set.length;
    $('#set-empty').hidden = state.set.length > 0;
    state.set.forEach(function (asset, index) {
      var thumbBox = el('div', { class: 'thumb-box' }, [
        asset.thumb ? el('img', { class: 'thumb', src: asset.thumb, alt: '' }) : el('div', { class: 'thumb' }),
        el('span', { class: 'num', text: String(index + 1) })
      ]);
      var open = !!ui.openExtras[asset.id];
      var meta = el('button', {
        type: 'button', class: 'meta', 'aria-expanded': open ? 'true' : 'false',
        onclick: function () { ui.openExtras[asset.id] = !ui.openExtras[asset.id]; render(); }
      }, [
        el('span', { class: 'name', text: asset.name }),
        el('span', { class: 'owner', text: (asset.mine ? 'Your upload' : 'From ' + asset.owner) + (asset.kind === 'video' && asset.duration_ms ? ' · ' + fmt(asset.duration_ms) : '') + (asset.markers.length ? ' · ' + asset.markers.length + ' marker' + (asset.markers.length > 1 ? 's' : '') : '') }),
        statusBadge(asset),
        el('span', { class: 'more', text: open ? 'Less' : 'Options' })
      ]);
      var up = el('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Move ' + asset.name + ' up', html: ICON.up, onclick: function () { move(index, -1); } });
      var down = el('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Move ' + asset.name + ' down', html: ICON.down, onclick: function () { move(index, 1); } });
      up.disabled = index === 0;
      down.disabled = index === state.set.length - 1;
      var row = el('div', { class: 'set-row' }, [thumbBox, meta, up, down]);
      var item = el('li', { class: 'set-item' }, [row]);
      if (open) item.appendChild(renderExtras(asset));
      list.appendChild(item);
    });
  }

  function renderExtras(asset) {
    var box = el('div', { class: 'extras' });
    if (asset.kind === 'video') {
      var player = null;
      if (asset.file) {
        player = el('video', { class: 'preview', src: asset.file, controls: '', preload: 'metadata', playsinline: '' });
        box.appendChild(player);
      }
      box.appendChild(el('div', { text: 'Start points the operator can jump to:' }));
      var markers = el('div', { class: 'markers' });
      asset.markers.forEach(function (m) {
        var chip = el('span', { class: 'marker' }, [el('span', { text: m.label + ' · ' + fmt(m.t_ms) })]);
        if (m.mine || asset.mine) {
          chip.appendChild(el('button', { type: 'button', 'aria-label': 'Remove marker ' + m.label, html: ICON.remove, onclick: function () { post('markers/delete', { marker_id: m.id }); } }));
        }
        markers.appendChild(chip);
      });
      if (!asset.markers.length) markers.appendChild(el('span', { class: 'owner', text: 'None yet.' }));
      box.appendChild(markers);
      var form = document.getElementById('tpl-marker-form').content.firstElementChild.cloneNode(true);
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var t = parseTime(form.elements.time.value);
        if (t === null) { form.elements.time.focus(); return; }
        var label = form.elements.label.value.trim() || 'Marker ' + fmt(t);
        post('markers/add', { asset_id: asset.id, label: label, t_ms: t });
      });
      box.appendChild(form);
      if (player) {
        box.appendChild(el('button', {
          type: 'button', class: 'link-btn', text: 'Add marker here (at the video\u2019s current time)',
          onclick: function () {
            player.pause();
            var t = Math.round(player.currentTime * 1000);
            form.elements.time.value = fmt(t);
            if (form.elements.label.value.trim()) {
              form.requestSubmit();
            } else {
              form.elements.label.placeholder = 'Name for ' + fmt(t) + ', then Add';
              form.elements.label.focus();
            }
          }
        }));
      }
    }
    if (asset.mine) {
      var cb = el('input', { type: 'checkbox' });
      cb.checked = asset.private;
      cb.addEventListener('change', function () { post('private', { asset_id: asset.id, private: cb.checked }); });
      box.appendChild(el('label', { class: 'private-toggle' }, [cb, el('span', { text: 'Keep private (other guests can’t pick it)' })]));
    }
    box.appendChild(el('div', {}, [el('button', { type: 'button', class: 'link-btn', text: 'Remove from my set', onclick: function () { post('set/remove', { asset_id: asset.id }); } })]));
    return box;
  }

  function move(index, delta) {
    var ids = state.set.map(function (a) { return a.id; });
    var target = index + delta;
    if (target < 0 || target >= ids.length) return;
    var tmp = ids[index]; ids[index] = ids[target]; ids[target] = tmp;
    post('set/order', { asset_ids: ids });
  }

  function renderLibrary() {
    var grid = $('#library');
    if (!grid) return;
    grid.innerHTML = '';
    var items = state.library.filter(function (a) {
      if (ui.filter === 'all') return true;
      return a.group === ui.filter;
    });
    $('#library-empty').hidden = items.length > 0;
    items.forEach(function (asset) {
      var selected = ui.selected.indexOf(asset.id) !== -1;
      var img = el('div', { class: 'img' }, [
        asset.thumb ? el('img', { src: asset.thumb, alt: '', loading: 'lazy' }) : null,
        asset.kind === 'video' ? el('span', { class: 'dur', text: fmt(asset.duration_ms) }) : null,
        el('span', { class: 'check', html: selected ? ICON.check : '' })
      ]);
      var card = el('button', {
        type: 'button', class: 'pick', 'aria-pressed': selected ? 'true' : 'false',
        'aria-label': (selected ? 'Deselect ' : 'Select ') + asset.name + ' from ' + asset.owner,
        onclick: function () {
          var at = ui.selected.indexOf(asset.id);
          if (at === -1) ui.selected.push(asset.id); else ui.selected.splice(at, 1);
          render();
        }
      }, [img, el('div', { class: 'txt' }, [
        el('div', { class: 'who', text: asset.mine ? 'Your upload' : asset.owner }),
        el('div', { class: 'when', text: asset.when })
      ])]);
      grid.appendChild(card);
    });
  }

  function renderTabs() {
    document.querySelectorAll('[data-tab]').forEach(function (btn) {
      btn.setAttribute('aria-selected', btn.dataset.tab === ui.tab ? 'true' : 'false');
    });
    document.querySelectorAll('[data-panel]').forEach(function (panel) {
      panel.hidden = panel.dataset.panel !== ui.tab;
    });
    document.querySelectorAll('[data-filter]').forEach(function (btn) {
      btn.setAttribute('aria-pressed', btn.dataset.filter === ui.filter ? 'true' : 'false');
    });
    var action = $('#main-action');
    if (ui.tab === 'library') {
      var n = ui.selected.length;
      action.textContent = n ? 'Add ' + n + ' to my set' : 'Select files to add';
      action.disabled = n === 0;
    } else {
      action.textContent = 'Done';
      action.disabled = false;
    }
  }

  function render() {
    renderTabs();
    renderSet();
    renderLibrary();
  }

  document.querySelectorAll('[data-tab]').forEach(function (btn) {
    btn.addEventListener('click', function () { ui.tab = btn.dataset.tab; render(); });
  });
  document.querySelectorAll('[data-filter]').forEach(function (btn) {
    btn.addEventListener('click', function () { ui.filter = btn.dataset.filter; render(); });
  });
  $('#main-action').addEventListener('click', function () {
    if (ui.tab === 'library' && ui.selected.length) {
      var ids = ui.selected.slice();
      ui.selected = [];
      ui.tab = 'mine';
      post('set/add', { asset_ids: ids });
    } else if (ui.tab !== 'mine') {
      ui.tab = 'mine';
      render();
    }
  });

  /* Resumable uploads (tus) */
  var input = document.getElementById('file-input');
  if (input) {
    input.addEventListener('change', function () {
      Array.prototype.forEach.call(input.files, startUpload);
      input.value = '';
    });
  }

  function startUpload(file) {
    var label = el('div', { text: file.name });
    var status = el('div', { class: 'owner', text: 'Starting…' });
    var bar = el('span');
    var row = el('li', { class: 'upload' }, [label, status, el('div', { class: 'bar' }, [bar])]);
    document.getElementById('uploads').prepend(row);
    if (file.size > maxBytes) {
      row.classList.add('error');
      status.textContent = 'Too large (limit ' + Math.round(maxBytes / 1048576) + ' MB).';
      return;
    }
    var upload = new tus.Upload(file, {
      endpoint: tusEndpoint,
      chunkSize: 8 * 1024 * 1024,
      retryDelays: [0, 1000, 3000, 5000, 10000, 20000, 30000, 60000],
      removeFingerprintOnSuccess: true,
      metadata: { filename: file.name, filetype: file.type || '' },
      onError: function (err) {
        row.classList.add('error');
        status.textContent = 'Upload stopped. Pick the file again to continue where it stopped. (' + (err && err.message ? err.message.split('\n')[0] : 'error') + ')';
      },
      onProgress: function (sent, total) {
        var pct = total ? Math.floor((sent / total) * 100) : 0;
        bar.style.width = pct + '%';
        status.textContent = pct + '% · ' + Math.round(sent / 1048576) + ' of ' + Math.round(total / 1048576) + ' MB';
      },
      onSuccess: function () {
        row.classList.add('done');
        bar.style.width = '100%';
        status.textContent = 'Uploaded. It is in your set and will be checked by the team.';
        load();
      }
    });
    upload.findPreviousUploads().then(function (previous) {
      if (previous.length) {
        upload.resumeFromPreviousUpload(previous[0]);
        status.textContent = 'Resuming…';
      }
      upload.start();
    });
  }

  load();
})();
