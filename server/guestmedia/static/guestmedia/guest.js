/* Guest page: My set / Upload / Library. Plain JS, no build step. */
(function () {
  'use strict';

  var root = document.getElementById('app');
  var token = root.dataset.token;
  var tusEndpoint = root.dataset.tus;
  var maxBytes = parseInt(root.dataset.maxMb, 10) * 1024 * 1024;
  var api = '/g/' + token + '/api/';
  // This device was signed out (or the PIN changed): reload to show the PIN screen.
  function checked(r) {
    if (r.status === 401 || r.status === 403) {
      location.reload();
      return new Promise(function () {});
    }
    return r.json();
  }

  // Every call is about the event this page shows (a person can be in several).
  var eventQuery = '?e=' + encodeURIComponent(root.dataset.event || '');

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
  function timed(a) { return a.kind === 'video' || a.kind === 'audio'; }
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
    check: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5 9-10"/></svg>',
    expand: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>',
    prev: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>',
    next: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>',
    trash: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/></svg>',
    heart: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M12 20s-7-4.4-9.2-8.6C1.2 8.3 3 5 6.3 5c2 0 3.3 1.1 4 2.3h3.4C14.4 6.1 15.7 5 17.7 5 21 5 22.8 8.3 21.2 11.4 19 15.6 12 20 12 20z"/></svg>',
    heartOn: '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M12 20s-7-4.4-9.2-8.6C1.2 8.3 3 5 6.3 5c2 0 3.3 1.1 4 2.3h3.4C14.4 6.1 15.7 5 17.7 5 21 5 22.8 8.3 21.2 11.4 19 15.6 12 20 12 20z"/></svg>',
    grip: '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="9" cy="5" r="1.7"/><circle cx="15" cy="5" r="1.7"/><circle cx="9" cy="12" r="1.7"/><circle cx="15" cy="12" r="1.7"/><circle cx="9" cy="19" r="1.7"/><circle cx="15" cy="19" r="1.7"/></svg>',
    close: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>'
  };

  /* Full-screen viewer: see a photo or video the way it will look on the 16:9 screen. */
  var viewer = null;
  function openViewer(list, index, title) {
    closeViewer();
    var last = document.activeElement;
    var stage = el('div', { class: 'viewer-stage' });
    var caption = el('div', { class: 'viewer-caption' });
    var prevBtn = el('button', { type: 'button', class: 'viewer-nav prev', 'aria-label': 'Previous', html: ICON.prev });
    var nextBtn = el('button', { type: 'button', class: 'viewer-nav next', 'aria-label': 'Next', html: ICON.next });
    var closeBtn = el('button', { type: 'button', class: 'viewer-close', 'aria-label': 'Close', html: ICON.close });
    var box = el('div', { class: 'viewer', role: 'dialog', 'aria-modal': 'true', 'aria-label': title }, [stage, prevBtn, nextBtn, closeBtn, caption]);
    function show(i) {
      index = (i + list.length) % list.length;
      var a = list[index];
      stage.innerHTML = '';
      var media;
      if (a.kind === 'video' && a.file) {
        media = el('video', { src: a.file, controls: '', autoplay: '', playsinline: '', poster: a.thumb || '' });
      } else if (a.kind === 'audio' && a.file) {
        media = el('div', { class: 'viewer-audio' }, [
          a.thumb ? el('img', { src: a.thumb, alt: '' }) : el('div', { class: 'note', text: '\u266A' }),
          el('audio', { src: a.file, controls: '', autoplay: '' })
        ]);
      } else if (a.kind === 'photo' && (a.file || a.thumb)) {
        media = el('img', { src: a.file || a.thumb, alt: a.name });
      } else {
        media = el('div', { class: 'viewer-wait', text: a.status === 'failed' ? 'This file could not be processed.' : 'Still being prepared… try again in a moment.' });
      }
      stage.appendChild(media);
      caption.textContent = (list.length > 1 ? (index + 1) + ' / ' + list.length + ' · ' : '') + a.name + ' · ' + (a.mine ? 'your upload' : 'from ' + a.owner);
      prevBtn.hidden = nextBtn.hidden = list.length < 2;
    }
    function key(e) {
      if (e.key === 'Escape') closeViewer();
      else if (e.key === 'ArrowLeft') show(index - 1);
      else if (e.key === 'ArrowRight') show(index + 1);
    }
    var startX = null;
    stage.addEventListener('touchstart', function (e) { startX = e.touches[0].clientX; }, { passive: true });
    stage.addEventListener('touchend', function (e) {
      if (startX === null) return;
      var dx = e.changedTouches[0].clientX - startX;
      startX = null;
      if (Math.abs(dx) > 50 && list.length > 1) show(index + (dx < 0 ? 1 : -1));
    });
    stage.addEventListener('click', function (e) { if (e.target === stage) closeViewer(); });
    prevBtn.addEventListener('click', function () { show(index - 1); });
    nextBtn.addEventListener('click', function () { show(index + 1); });
    closeBtn.addEventListener('click', closeViewer);
    document.addEventListener('keydown', key);
    document.body.appendChild(box);
    document.body.classList.add('viewer-open');
    viewer = { box: box, key: key, last: last };
    show(index);
    closeBtn.focus();
  }
  function closeViewer() {
    if (!viewer) return;
    document.removeEventListener('keydown', viewer.key);
    viewer.box.remove();
    document.body.classList.remove('viewer-open');
    if (viewer.last && viewer.last.focus) viewer.last.focus();
    viewer = null;
  }

  function post(path, body) {
    return fetch(api + path + eventQuery, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(checked).then(setState);
  }
  function load() {
    return fetch(api + 'state' + eventQuery).then(checked).then(setState);
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
      var thumbBox = el('button', {
        type: 'button', class: 'thumb-box', 'aria-label': 'View ' + asset.name + ' full screen',
        onclick: function () { openViewer(state.set, index, 'My set'); }
      }, [
        asset.thumb ? el('img', { class: 'thumb', src: asset.thumb, alt: '' }) : el('div', { class: 'thumb' }),
        el('span', { class: 'num', text: String(index + 1) }),
        el('span', { class: 'zoom', html: ICON.expand })
      ]);
      var open = !!ui.openExtras[asset.id];
      var meta = el('button', {
        type: 'button', class: 'meta', 'aria-expanded': open ? 'true' : 'false',
        onclick: function () { ui.openExtras[asset.id] = !ui.openExtras[asset.id]; render(); }
      }, [
        el('span', { class: 'name', text: asset.name }),
        el('span', { class: 'owner', text: (asset.mine ? 'Your upload' : 'From ' + asset.owner) + (timed(asset) && asset.duration_ms ? ' · ' + fmt(asset.duration_ms) : '') + (asset.markers.length ? ' · ' + asset.markers.length + ' marker' + (asset.markers.length > 1 ? 's' : '') : '') }),
        statusBadge(asset),
        el('span', { class: 'more', text: open ? 'Less' : 'Options' })
      ]);
      var grip = el('button', {
        type: 'button', class: 'grip', title: 'Drag to change the order',
        'aria-label': 'Move ' + asset.name + ' (position ' + (index + 1) + ' of ' + state.set.length + '): drag, or use the arrow keys',
        html: ICON.grip,
        onkeydown: function (e) {
          if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            e.preventDefault();
            ui.focusGrip = asset.id;
            move(index, e.key === 'ArrowUp' ? -1 : 1);
          }
        },
        onpointerdown: function (e) { startDrag(e, item); }
      });
      var remove = el('button', {
        type: 'button', class: 'icon-btn remove-btn', title: 'Remove from my set',
        'aria-label': 'Remove ' + asset.name + ' from my set', html: ICON.trash,
        onclick: function () { quickRemove(asset); }
      });
      var row = el('div', { class: 'set-row' }, [grip, thumbBox, meta, heartButton(asset, 'icon-btn row-heart'), remove]);
      var item = el('li', { class: 'set-item', 'data-id': String(asset.id) }, [row]);
      if (open) item.appendChild(renderExtras(asset));
      list.appendChild(item);
      if (ui.focusGrip === asset.id) { ui.focusGrip = null; setTimeout(function () { grip.focus(); }, 0); }
    });
  }

  function renderExtras(asset) {
    var box = el('div', { class: 'extras' });
    if (timed(asset)) {
      var player = null;
      if (asset.file) {
        player = el(asset.kind === 'audio' ? 'audio' : 'video', { class: 'preview', src: asset.file, controls: '', preload: 'metadata', playsinline: '' });
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
          type: 'button', class: 'link-btn', text: 'Add marker here (at the player\u2019s current time)',
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
    var at = state.set.indexOf(asset);
    var moveUp = el('button', { type: 'button', class: 'link-btn', text: 'Move up', onclick: function () { move(at, -1); } });
    var moveDown = el('button', { type: 'button', class: 'link-btn', text: 'Move down', onclick: function () { move(at, 1); } });
    if (at <= 0) moveUp.disabled = true;
    if (at === state.set.length - 1) moveDown.disabled = true;
    box.appendChild(el('div', { class: 'extra-actions' }, [moveUp, moveDown,
      el('button', { type: 'button', class: 'link-btn', text: 'Remove from my set', onclick: function () { post('set/remove', { asset_id: asset.id }); } })]));
    return box;
  }

  /* Drag to reorder (pointer events: mouse, touch and pen). The list moves live under the finger. */
  function startDrag(e, item) {
    if (e.button !== undefined && e.button !== 0) return;
    if (state.set.length < 2) return;
    e.preventDefault();
    var list = item.parentNode;
    var handle = e.currentTarget;
    var before = Array.prototype.map.call(list.children, function (li) { return li.dataset.id; }).join(',');
    var rect = item.getBoundingClientRect();
    var grabOffset = e.clientY - rect.top;
    var pointerY = e.clientY;
    var scrollTimer = null;
    ui.dragging = true;
    item.classList.add('dragging');
    list.classList.add('sorting');
    try { handle.setPointerCapture(e.pointerId); } catch (err) { /* old browsers */ }
    if (navigator.vibrate) navigator.vibrate(15);

    function place() {
      item.style.transform = '';
      var natural = item.getBoundingClientRect().top;
      var siblings = Array.prototype.filter.call(list.children, function (li) { return li !== item; });
      var top = pointerY - grabOffset;
      var mid = top + item.offsetHeight / 2;
      var target = null;
      for (var i = 0; i < siblings.length; i++) {
        var r = siblings[i].getBoundingClientRect();
        if (mid < r.top + r.height / 2) { target = siblings[i]; break; }
      }
      if (target !== item.nextSibling) {
        list.insertBefore(item, target);
        natural = item.getBoundingClientRect().top;
      }
      item.style.transform = 'translateY(' + (top - natural) + 'px)';
    }
    function autoScroll() {
      var edge = 70;
      var speed = 0;
      if (pointerY < edge) speed = -Math.ceil((edge - pointerY) / 6);
      else if (pointerY > window.innerHeight - edge) speed = Math.ceil((pointerY - (window.innerHeight - edge)) / 6);
      if (speed) { window.scrollBy(0, speed); place(); }
    }
    var pointerId = e.pointerId;
    var ended = false;
    function moveHandler(ev) {
      if (ev.pointerId !== pointerId) return;
      if (ev.pointerType === 'mouse' && ev.buttons === 0) { end(); return; } // released outside the window
      pointerY = ev.clientY;
      place();
    }
    function upHandler(ev) { if (ev.pointerId === pointerId) end(); }
    function end() {
      if (ended) return;
      ended = true;
      window.removeEventListener('pointermove', moveHandler);
      window.removeEventListener('pointerup', upHandler);
      window.removeEventListener('pointercancel', upHandler);
      window.removeEventListener('blur', end);
      handle.removeEventListener('lostpointercapture', end);
      clearInterval(scrollTimer);
      item.style.transform = '';
      item.classList.remove('dragging');
      list.classList.remove('sorting');
      ui.dragging = false;
      var ids = Array.prototype.map.call(list.children, function (li) { return li.dataset.id; });
      if (ids.join(',') !== before) {
        var byId = {};
        state.set.forEach(function (a) { byId[a.id] = a; });
        state.set = ids.map(function (id) { return byId[id]; });
        render();
        post('set/order', { asset_ids: ids.map(Number) });
      } else {
        render();
      }
    }
    // On the window, not the handle: moving sideways off the handle must not lose the drag.
    window.addEventListener('pointermove', moveHandler);
    window.addEventListener('pointerup', upHandler);
    window.addEventListener('pointercancel', upHandler);
    window.addEventListener('blur', end);
    handle.addEventListener('lostpointercapture', end);
    scrollTimer = setInterval(autoScroll, 16);
    place();
  }

  /* ♥ favorites: kept per person across events. */
  function heartButton(asset, cls) {
    return el('button', {
      type: 'button', class: (cls || 'icon-btn') + ' heart-btn' + (asset.fav ? ' on' : ''),
      'aria-pressed': asset.fav ? 'true' : 'false',
      title: asset.fav ? 'Remove from favorites' : 'Add to favorites',
      'aria-label': (asset.fav ? 'Remove ' : 'Add ') + asset.name + (asset.fav ? ' from' : ' to') + ' favorites',
      html: asset.fav ? ICON.heartOn : ICON.heart,
      onclick: function (e) { e.stopPropagation(); toggleFavorite(asset); }
    });
  }
  function toggleFavorite(asset) {
    var on = !asset.fav;
    [state.set, state.library, state.favorites || []].forEach(function (list) {
      list.forEach(function (a) { if (a.id === asset.id) a.fav = on; });
    });
    render();
    post('favorite', { asset_id: asset.id, on: on });
  }

  /* Remove at once, with Undo (puts it back in the same place). */
  function quickRemove(asset) {
    var order = state.set.map(function (a) { return a.id; });
    state.set = state.set.filter(function (a) { return a.id !== asset.id; });
    render();
    post('set/remove', { asset_id: asset.id });
    showUndo('Removed “' + asset.name + '”', function () {
      post('set/add', { asset_ids: [asset.id] }).then(function () {
        var now = state.set.map(function (a) { return a.id; });
        var wanted = order.filter(function (id) { return now.indexOf(id) !== -1; });
        if (wanted.join(',') !== now.join(',')) post('set/order', { asset_ids: wanted });
      });
    });
  }
  var undoTimer = null;
  function showUndo(text, undo) {
    var old = document.getElementById('undo-bar');
    if (old) old.remove();
    clearTimeout(undoTimer);
    var bar = el('div', { id: 'undo-bar', class: 'undo-bar', role: 'status' }, [
      el('span', { text: text }),
      el('button', { type: 'button', text: 'Undo', onclick: function () { bar.remove(); clearTimeout(undoTimer); undo(); } })
    ]);
    document.body.appendChild(bar);
    undoTimer = setTimeout(function () { bar.remove(); }, 6000);
  }

  function move(index, delta) {
    var ids = state.set.map(function (a) { return a.id; });
    var target = index + delta;
    if (target < 0 || target >= ids.length) return;
    var tmp = ids[index]; ids[index] = ids[target]; ids[target] = tmp;
    post('set/order', { asset_ids: ids });
  }

  function renderLibrary() {
    renderPicker($('#library'), $('#library-empty'), state.library.filter(function (a) {
      if (ui.filter === 'all') return true;
      return a.group === ui.filter;
    }), 'Library');
    renderPicker($('#favorites'), $('#favorites-empty'), state.favorites || [], 'Favorites');
    var fc = document.getElementById('fav-count');
    if (fc) fc.textContent = (state.favorites || []).length;
  }

  function renderPicker(grid, empty, items, title) {
    if (!grid) return;
    grid.innerHTML = '';
    if (empty) empty.hidden = items.length > 0;
    items.forEach(function (asset) {
      if (asset.in_set) {
        // Already in the set (favorites tab): show it, but it can't be added twice.
        var inImg = el('div', { class: 'img' }, [
          asset.thumb ? el('img', { src: asset.thumb, alt: '', loading: 'lazy' }) : null,
          el('span', { class: 'in-set', text: 'In my set' })
        ]);
        var inCard = el('div', { class: 'pick in' }, [inImg, el('div', { class: 'txt' }, [
          el('div', { class: 'who', text: asset.mine ? 'Your upload' : asset.owner }),
          el('div', { class: 'when', text: asset.when })
        ])]);
        grid.appendChild(el('div', { class: 'pick-wrap' }, [inCard,
          el('button', { type: 'button', class: 'pick-zoom', 'aria-label': 'View ' + asset.name + ' full screen', html: ICON.expand,
            onclick: function () { openViewer(items, items.indexOf(asset), title); } }),
          heartButton(asset, 'pick-heart')]));
        return;
      }
      var selected = ui.selected.indexOf(asset.id) !== -1;
      var img = el('div', { class: 'img' }, [
        asset.thumb ? el('img', { src: asset.thumb, alt: '', loading: 'lazy' }) : null,
        timed(asset) ? el('span', { class: 'dur', text: (asset.kind === 'audio' ? '\u266A ' : '') + fmt(asset.duration_ms) }) : null,
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
      var zoom = el('button', {
        type: 'button', class: 'pick-zoom', 'aria-label': 'View ' + asset.name + ' full screen', html: ICON.expand,
        onclick: function () { openViewer(items, items.indexOf(asset), title); }
      });
      grid.appendChild(el('div', { class: 'pick-wrap' }, [card, zoom, heartButton(asset, 'pick-heart')]));
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
    if (ui.tab === 'library' || ui.tab === 'favorites') {
      var n = ui.selected.length;
      action.textContent = n ? 'Add ' + n + ' to my set' : 'Select files to add';
      action.disabled = n === 0;
    } else {
      action.textContent = 'Done';
      action.disabled = false;
    }
  }

  function renderPrevious() {
    var box = document.getElementById('copy-prev');
    var prev = state.previous_set;
    box.hidden = !prev || state.set.length > 0;
    if (prev) {
      document.getElementById('copy-prev-text').textContent =
        'Start from your set of ' + prev.label + ' (' + prev.count + ' file' + (prev.count === 1 ? '' : 's') + ')?';
    }
    var shared = document.getElementById('filter-shared');
    if (shared) shared.hidden = !state.library.some(function (a) { return a.group === 'shared'; });
  }

  function render() {
    if (ui.dragging) return; // refreshed after the drop
    renderTabs();
    renderSet();
    renderLibrary();
    renderPrevious();
  }

  document.getElementById('copy-prev-btn').addEventListener('click', function () {
    if (state.previous_set) post('set/copy', { set_id: state.previous_set.id });
  });

  document.querySelectorAll('[data-tab]').forEach(function (btn) {
    btn.addEventListener('click', function () { ui.tab = btn.dataset.tab; render(); });
  });
  document.querySelectorAll('[data-filter]').forEach(function (btn) {
    btn.addEventListener('click', function () { ui.filter = btn.dataset.filter; render(); });
  });
  $('#main-action').addEventListener('click', function () {
    if ((ui.tab === 'library' || ui.tab === 'favorites') && ui.selected.length) {
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
