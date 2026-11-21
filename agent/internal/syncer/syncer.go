// Package syncer downloads the files in the manifest to the vMix PC.
//
// Files go to <media>/<guest>/<id>-<name>.<ext>. Downloads resume after a
// dropout and are checked against the server's SHA-256 before vMix sees them.
// While something is live the download speed is limited, so playback never
// competes with a large download.
package syncer

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/silvansan/vitrine/agent/internal/model"
	"github.com/silvansan/vitrine/agent/internal/serverapi"
	"github.com/silvansan/vitrine/agent/internal/store"
)

type Status struct {
	Total        int    `json:"total"`
	Done         int    `json:"done"`
	PendingBytes int64  `json:"pending_bytes"`
	Current      string `json:"current"`
	LastError    string `json:"last_error"`
	// Asset ids still to download (the server warns staff when an event's files aren't all here).
	Missing []int64 `json:"missing"`
	// Manifest version these numbers are for.
	ManifestVersion int `json:"manifest_version"`
}

type Syncer struct {
	Client    *serverapi.Client
	Store     *store.Store
	MediaDir  string
	DataDir   string
	Throttle  func() int // KB/s limit right now, 0 = none
	InUse     func() map[int64]bool
	OnChanged func()

	mu      sync.Mutex
	running bool
	again   bool
	status  Status
}

func (s *Syncer) Status() Status {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := s.status
	out.Missing = append([]int64{}, s.status.Missing...)
	return out
}

// Trigger starts a sync, or queues one if a sync is already running.
func (s *Syncer) Trigger(ctx context.Context) {
	s.mu.Lock()
	if s.running {
		s.again = true
		s.mu.Unlock()
		return
	}
	s.running = true
	s.mu.Unlock()
	go func() {
		for {
			s.run(ctx)
			s.mu.Lock()
			if !s.again || ctx.Err() != nil {
				s.running = false
				s.mu.Unlock()
				return
			}
			s.again = false
			s.mu.Unlock()
		}
	}()
}

func safeSegment(text string) string {
	text = strings.Map(func(r rune) rune {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9', r == '-', r == '_', r == '.':
			return r
		}
		return '-'
	}, text)
	text = strings.Trim(text, ".-")
	if text == "" {
		return "file"
	}
	return text
}

// LocalPath is where an asset's main file lives.
func (s *Syncer) LocalPath(a *model.Asset) string {
	return filepath.Join(s.MediaDir, safeSegment(a.OwnerSlug), safeSegment(a.LocalName))
}

func short(sha string) string {
	if len(sha) > 10 {
		return sha[:10]
	}
	return sha
}

func fileSHA(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func (s *Syncer) setStatus(fn func(st *Status)) {
	s.mu.Lock()
	fn(&s.status)
	s.mu.Unlock()
}

func (s *Syncer) run(ctx context.Context) {
	manifest := s.Store.Manifest()
	if manifest == nil {
		return
	}
	assets := manifest.Assets
	var pending int64
	done := 0
	missing := []int64{}
	var files map[int64]store.LocalFile
	s.Store.Read(func(st *store.State) {
		files = map[int64]store.LocalFile{}
		for id, f := range st.Files {
			files[id] = *f
		}
	})
	for _, a := range assets {
		if f, ok := files[a.ID]; ok && f.SHA256 == a.SHA256 && exists(f.Path) && exists(f.Thumb) {
			done++
		} else {
			pending += a.Size
			missing = append(missing, a.ID)
		}
	}
	s.setStatus(func(st *Status) {
		st.Total = len(assets)
		st.Done = done
		st.PendingBytes = pending
		st.LastError = ""
		st.Missing = missing
		st.ManifestVersion = manifest.Version
	})

	for i := range assets {
		if ctx.Err() != nil {
			return
		}
		a := &assets[i]
		if f, ok := files[a.ID]; ok && f.SHA256 == a.SHA256 && exists(f.Path) && exists(f.Thumb) {
			continue
		}
		s.setStatus(func(st *Status) { st.Current = a.Name })
		local, err := s.fetch(ctx, a)
		if err != nil {
			log.Printf("sync %s: %v", a.Name, err)
			s.setStatus(func(st *Status) { st.LastError = fmt.Sprintf("%s: %v", a.Name, err) })
			continue
		}
		_ = s.Store.Update(func(st *store.State) { st.Files[a.ID] = local })
		s.setStatus(func(st *Status) {
			st.Done++
			st.PendingBytes -= a.Size
			kept := st.Missing[:0]
			for _, id := range st.Missing {
				if id != a.ID {
					kept = append(kept, id)
				}
			}
			st.Missing = kept
		})
		if s.OnChanged != nil {
			s.OnChanged()
		}
	}
	s.setStatus(func(st *Status) { st.Current = "" })
	s.cleanup(manifest)
	if s.OnChanged != nil {
		s.OnChanged()
	}
}

func exists(path string) bool {
	if path == "" {
		return false
	}
	_, err := os.Stat(path)
	return err == nil
}

func (s *Syncer) fetch(ctx context.Context, a *model.Asset) (*store.LocalFile, error) {
	dest := s.LocalPath(a)
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return nil, err
	}
	// The file may already be complete from an earlier run.
	if sha, err := fileSHA(dest); err != nil || sha != a.SHA256 {
		tmp := dest + ".new"
		for attempt := 0; attempt < 5; attempt++ {
			err = s.Client.Download(ctx, a.File, tmp, s.limited(ctx))
			if err == nil {
				break
			}
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			time.Sleep(time.Duration(attempt+1) * 2 * time.Second)
		}
		if err != nil {
			return nil, err
		}
		sha, err := fileSHA(tmp)
		if err != nil {
			return nil, err
		}
		if sha != a.SHA256 {
			_ = os.Remove(tmp)
			return nil, fmt.Errorf("checksum mismatch, will download again")
		}
		_ = os.Remove(dest)
		if err := os.Rename(tmp, dest); err != nil {
			return nil, err
		}
	}

	local := &store.LocalFile{Path: dest, SHA256: a.SHA256}
	thumbDir := filepath.Join(s.DataDir, "thumbs")
	_ = os.MkdirAll(thumbDir, 0o755)
	local.Thumb = filepath.Join(thumbDir, fmt.Sprintf("%d-%s.jpg", a.ID, short(a.SHA256)))
	if !exists(local.Thumb) {
		if err := s.Client.Download(ctx, a.Thumb, local.Thumb, nil); err != nil {
			return nil, fmt.Errorf("thumbnail: %w", err)
		}
	}
	if a.Filmstrip != "" {
		local.Filmstrip = filepath.Join(thumbDir, fmt.Sprintf("%d-%s-strip.jpg", a.ID, short(a.SHA256)))
		if !exists(local.Filmstrip) {
			if err := s.Client.Download(ctx, a.Filmstrip, local.Filmstrip, nil); err != nil {
				log.Printf("filmstrip %s: %v", a.Name, err)
				local.Filmstrip = ""
			}
		}
	}
	return local, nil
}

// cleanup removes files for assets that left the manifest, unless vMix is using them.
func (s *Syncer) cleanup(m *model.Manifest) {
	keep := map[int64]bool{}
	for _, a := range m.Assets {
		keep[a.ID] = true
	}
	inUse := map[int64]bool{}
	if s.InUse != nil {
		inUse = s.InUse()
	}
	var remove []store.LocalFile
	_ = s.Store.Update(func(st *store.State) {
		for id, f := range st.Files {
			if keep[id] || inUse[id] {
				continue
			}
			remove = append(remove, *f)
			delete(st.Files, id)
			delete(st.Positions, id)
		}
	})
	for _, f := range remove {
		for _, p := range []string{f.Path, f.Thumb, f.Filmstrip} {
			if p != "" {
				_ = os.Remove(p)
			}
		}
	}
}

// limited wraps a reader so it doesn't exceed the current throttle.
func (s *Syncer) limited(ctx context.Context) func(io.Reader) io.Reader {
	return func(r io.Reader) io.Reader {
		return &throttledReader{ctx: ctx, r: r, limit: s.Throttle, start: time.Now()}
	}
}

type throttledReader struct {
	ctx   context.Context
	r     io.Reader
	limit func() int
	start time.Time
	count int64
}

func (t *throttledReader) Read(p []byte) (int, error) {
	kbps := 0
	if t.limit != nil {
		kbps = t.limit()
	}
	if kbps <= 0 {
		t.start = time.Now()
		t.count = 0
		return t.r.Read(p)
	}
	if len(p) > 64*1024 {
		p = p[:64*1024]
	}
	n, err := t.r.Read(p)
	t.count += int64(n)
	allowed := time.Duration(float64(t.count) / float64(kbps*1024) * float64(time.Second))
	if wait := allowed - time.Since(t.start); wait > 0 {
		select {
		case <-t.ctx.Done():
			return n, t.ctx.Err()
		case <-time.After(wait):
		}
	}
	if time.Since(t.start) > 10*time.Second {
		t.start = time.Now()
		t.count = 0
	}
	return n, err
}
