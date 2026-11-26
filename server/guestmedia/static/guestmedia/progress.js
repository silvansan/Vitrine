/* Library tiles of videos being converted: a progress bar with the time left, then the thumbnail. */
(function () {
  var script = document.currentScript;
  var url = script && script.dataset.url;
  if (!url) return;
  var timer = null;

  function boxes() { return Array.prototype.slice.call(document.querySelectorAll('[data-progress]')); }

  function show(box, info) {
    if (!box.querySelector('.conv')) {
      box.textContent = '';
      box.classList.add('converting');
      box.innerHTML = '<div class="conv"><span class="conv-text"></span><span class="conv-bar"><i></i></span></div>';
    }
    box.querySelector('.conv-text').textContent = info.text;
    box.querySelector('.conv-bar i').style.width = Math.max(2, info.pct) + '%';
    var pill = document.querySelector('[data-progress-pill="' + box.dataset.progress + '"]');
    if (pill) pill.textContent = info.pct ? info.pct + '%' : 'Processing';
  }

  function finished(box, info) {
    var pill = document.querySelector('[data-progress-pill="' + box.dataset.progress + '"]');
    if (info.status === 'ready') {
      var img = document.createElement('img');
      img.alt = '';
      img.src = box.dataset.thumb + '?v=' + Date.now();
      box.replaceWith(img);
      if (pill) pill.remove();
    } else {
      box.removeAttribute('data-progress');
      box.textContent = info.status === 'failed' ? 'Failed' : info.status;
      if (pill) pill.textContent = box.textContent;
    }
  }

  function poll() {
    var list = boxes();
    if (!list.length) return;
    var ids = list.map(function (b) { return b.dataset.progress; }).join(',');
    fetch(url + '?ids=' + ids, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (data) {
        list.forEach(function (box) {
          var info = data[box.dataset.progress];
          if (!info) return;
          if (info.status === 'processing') show(box, info); else finished(box, info);
        });
      })
      .catch(function () {})
      .then(function () { timer = setTimeout(poll, document.hidden ? 6000 : 2000); });
  }

  if (boxes().length) poll();
})();
