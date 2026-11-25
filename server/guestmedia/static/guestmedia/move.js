/* Settings → Move to another server: live progress, and uploading an archive to import. */
(function () {
  var card = document.getElementById('move');
  if (!card) return;

  function size(n) {
    if (!n) return '0 MB';
    return n >= 1073741824 ? (n / 1073741824).toFixed(1) + ' GB' : Math.round(n / 1048576) + ' MB';
  }

  // While something runs, poll; reload the page when a step finishes.
  var running = card.querySelector('progress');
  if (running) {
    var last = null;
    var poll = setInterval(function () {
      fetch(card.dataset.status, { credentials: 'same-origin' }).then(function (r) {
        if (r.status !== 200 || r.redirected) { // signed out by the import (the database was replaced)
          clearInterval(poll);
          location.href = '/login/?next=' + encodeURIComponent(location.pathname + '#move');
          return null;
        }
        return r.json();
      }).then(function (d) {
        if (!d) return;
        ['export', 'import'].forEach(function (kind) {
          var s = d[kind] || {};
          var box = card.querySelector('[data-move="' + kind + '"]');
          var bar = box && box.querySelector('progress');
          if (!bar) return;
          if (s.state && s.state !== 'running') { clearInterval(poll); location.reload(); return; }
          if (s.total_bytes) { bar.max = s.total_bytes; bar.value = s.done_bytes || 0; }
          var strong = box.querySelector('strong');
          if (strong && s.step) strong.textContent = s.step;
          var muted = box.querySelector('p .muted');
          if (muted && s.total_bytes) muted.textContent = size(s.done_bytes) + ' of ' + size(s.total_bytes);
        });
        var key = JSON.stringify(d);
        last = key;
      }).catch(function () { /* the server restarts or the network blinks: try again */ });
    }, 2000);
  }

  // Upload of an archive to import (tus, resumable).
  var box = card.querySelector('.move-upload');
  if (!box || !window.tus) return;
  var input = box.querySelector('[data-move-file]');
  var list = box.querySelector('[data-move-list]');
  var drop = box.querySelector('.drop');
  var confirmField = card.querySelector('[data-move-confirm]');

  function start(file) {
    if (!confirmField || confirmField.value.trim().toUpperCase() !== 'REPLACE') {
      alert('Type REPLACE in the confirmation field first: importing replaces everything on this server.');
      if (confirmField) confirmField.focus();
      return;
    }
    if (!/\.tar$/i.test(file.name)) {
      alert('Choose the .tar archive made under "Move to another server" on the old server.');
      return;
    }
    list.innerHTML = '';
    var li = document.createElement('li');
    var name = document.createElement('strong');
    name.textContent = file.name;
    var status = document.createElement('span');
    status.className = 'muted';
    status.textContent = 'Starting…';
    var bar = document.createElement('div');
    bar.className = 'bar';
    var fill = document.createElement('i');
    bar.appendChild(fill);
    li.appendChild(name); li.appendChild(status); li.appendChild(bar);
    list.appendChild(li);
    var upload = new tus.Upload(file, {
      endpoint: box.dataset.endpoint,
      chunkSize: 32 * 1024 * 1024,
      retryDelays: [0, 2000, 5000, 10000, 20000, 30000],
      headers: { 'X-CSRFToken': box.dataset.csrf },
      metadata: { filename: file.name, filetype: 'application/x-tar' },
      onError: function (err) {
        li.className = 'bad';
        status.textContent = 'Stopped: ' + (err && err.message ? err.message.split('\n')[0] : 'error') + '. Choose the file again to continue where it stopped.';
      },
      onProgress: function (sent, total) {
        fill.style.width = (total ? Math.floor(sent / total * 100) : 0) + '%';
        status.textContent = size(sent) + ' of ' + size(total);
      },
      onSuccess: function () {
        li.className = 'ok';
        fill.style.width = '100%';
        status.textContent = 'Uploaded. Importing…';
        setTimeout(function () { location.reload(); }, 1500);
      }
    });
    upload.findPreviousUploads().then(function (previous) {
      if (previous.length) upload.resumeFromPreviousUpload(previous[0]);
      upload.start();
    });
  }
  input.addEventListener('change', function () { if (input.files[0]) start(input.files[0]); input.value = ''; });
  ['dragenter', 'dragover'].forEach(function (t) { drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add('over'); }); });
  ['dragleave', 'drop'].forEach(function (t) { drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.remove('over'); }); });
  drop.addEventListener('drop', function (e) { if (e.dataTransfer.files[0]) start(e.dataTransfer.files[0]); });
})();
