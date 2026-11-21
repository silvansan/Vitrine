/* Staff uploads in /manage/ (library and person pages), resumable with tus. */
(function () {
  var box = document.getElementById('upload');
  if (!box || !window.tus) return;
  var input = box.querySelector('[data-files]');
  var list = box.querySelector('[data-list]');
  var drop = box.querySelector('.drop');
  var personField = box.querySelector('[data-field=person]');
  var eventField = box.querySelector('[data-field=event]');
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
      status.textContent = 'Too large (limit ' + mb(maxBytes) + ' MB).';
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
        status.textContent = 'Stopped: ' + (err && err.message ? err.message.split('\n')[0] : 'error') + '. Choose the file again to continue.';
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
