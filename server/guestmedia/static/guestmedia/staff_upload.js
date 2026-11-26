/* Staff uploads in /manage/ (library and person pages), resumable with tus. */
(function () {
  var box = document.getElementById('upload');
  if (!box || !window.tus) return;
  var input = box.querySelector('[data-files]');
  var list = box.querySelector('[data-list]');
  var drop = box.querySelector('.drop');
  var personField = box.querySelector('[data-field=person]');
  var eventField = box.querySelector('[data-field=event]');
  function gb(bytes) { return (bytes / 1073741824).toFixed(1).replace(/\.0$/, ''); }
  // The server's own explanation (e.g. too large, empty file), else tus' message.
  function uploadErrorText(err) {
    var res = err && err.originalResponse;
    var body = res && res.getBody ? res.getBody() : '';
    if (res && res.getStatus && res.getStatus() === 413 && body) return body;
    return err && err.message ? err.message.split('\n')[0] : 'error';
  }
  var maxBytes = parseInt(box.dataset.maxMb, 10) * 1048576;
  var running = 0;
  var finished = 0;

  function endpoint() {
    var q = [];
    if (personField && personField.value) q.push('person=' + encodeURIComponent(personField.value));
    if (eventField && eventField.value) q.push('event=' + encodeURIComponent(eventField.value));
    return box.dataset.endpoint + (q.length ? '?' + q.join('&') : '');
  }

  function checkEventNeedsPerson() {
    if (!eventField || !personField || personField.type === 'hidden') return true;
    if (eventField.value && !personField.value) {
      alert('To put files in a set, choose the person too.');
      return false;
    }
    return true;
  }

  function mb(n) { return Math.round(n / 1048576); }

  function start(file) {
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
    li.appendChild(name);
    li.appendChild(status);
    li.appendChild(bar);
    list.appendChild(li);
    if (file.size > maxBytes) {
      status.textContent = 'Too large: ' + gb(file.size) + ' GB, the limit is ' + gb(maxBytes) + ' GB (Settings → Storage).';
      li.className = 'bad';
      return;
    }
    if (!file.size) {
      status.textContent = 'This file is empty (0 bytes). If it is in iCloud, OneDrive or Google Drive, download it first.';
      li.className = 'bad';
      return;
    }
    running++;
    var upload = new tus.Upload(file, {
      endpoint: endpoint(),
      chunkSize: 8 * 1024 * 1024,
      retryDelays: [0, 2000, 5000, 10000, 20000],
      headers: { 'X-CSRFToken': box.dataset.csrf },
      metadata: { filename: file.name, filetype: file.type },
      onError: function (err) {
        running--;
        li.className = 'bad';
        var why = uploadErrorText(err);
        status.textContent = err && err.originalResponse && err.originalResponse.getStatus() === 413
          ? why : 'Stopped: ' + why + '. Choose the file again to continue.';
      },
      onProgress: function (sent, total) {
        var pct = total ? Math.floor(sent / total * 100) : 0;
        fill.style.width = pct + '%';
        status.textContent = pct + '% · ' + mb(sent) + ' of ' + mb(total) + ' MB';
      },
      onSuccess: function () {
        running--;
        finished++;
        li.className = 'ok';
        fill.style.width = '100%';
        status.textContent = 'Uploaded, now being prepared for vMix.';
        if (running === 0) {
          var more = document.createElement('li');
          more.innerHTML = '<a href="">Reload the page</a> to see ' + (finished === 1 ? 'it' : 'them') + '.';
          list.appendChild(more);
        }
      }
    });
    upload.findPreviousUploads().then(function (previous) {
      if (previous.length) upload.resumeFromPreviousUpload(previous[0]);
      upload.start();
    });
  }

  function take(files) {
    if (!files || !files.length || !checkEventNeedsPerson()) return;
    Array.prototype.forEach.call(files, start);
  }

  input.addEventListener('change', function () { take(input.files); input.value = ''; });
  ['dragenter', 'dragover'].forEach(function (t) {
    drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add('over'); });
  });
  ['dragleave', 'drop'].forEach(function (t) {
    drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.remove('over'); });
  });
  drop.addEventListener('drop', function (e) { take(e.dataTransfer.files); });
  window.addEventListener('beforeunload', function (e) {
    if (running > 0) { e.preventDefault(); e.returnValue = ''; }
  });
})();
