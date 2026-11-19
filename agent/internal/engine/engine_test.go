package engine

import (
	"context"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/silvansan/vitrine/agent/internal/config"
	"github.com/silvansan/vitrine/agent/internal/store"
	"github.com/silvansan/vitrine/agent/internal/vmix"
	"github.com/silvansan/vitrine/agent/internal/vmix/vmixfake"
)

func setup(t *testing.T) (*Engine, *vmixfake.Fake) {
	t.Helper()
	fake := vmixfake.New()
	srv := httptest.NewServer(fake)
	t.Cleanup(srv.Close)
	st, err := store.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	_ = st.Update(func(s *store.State) {
		s.Settings.DefaultInputKey = "key-0001"
		s.Settings.DefaultInputTitle = "Banner Sunday"
		s.Settings.TransitionMs = 50
	})
	media := map[int64]*Media{
		1: {ID: 1, Path: `C:\Vitrine\media\maria\1-photo.jpg`, Kind: "photo"},
		2: {ID: 2, Path: `C:\Vitrine\media\daniel\2-trip.mp4`, Kind: "video", DurationMs: 20000},
	}
	e := &Engine{VMix: vmix.New(srv.URL), Store: st, Cfg: &config.Config{MixParamOffset: -1},
		Resolve: func(id int64) (*Media, bool) { m, ok := media[id]; return m, ok }}
	return e, fake
}

func TestTakeClearAndSeek(t *testing.T) {
	e, fake := setup(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go e.Run(ctx)

	if err := e.Take(1, 0); err != nil {
		t.Fatal(err)
	}
	if got := fake.Active(2); got != "GM_A" {
		t.Fatalf("mix 2 shows %q, want GM_A", got)
	}
	if fake.Active(1) != "Camera 1" {
		t.Fatalf("main output changed: %q", fake.Active(1))
	}

	if err := e.Take(2, 5000); err != nil {
		t.Fatal(err)
	}
	if got := fake.Active(2); got != "GM_B" {
		t.Fatalf("second take should use GM_B, got %q", got)
	}
	if pos := fake.Position("GM_B"); pos < 5000 {
		t.Fatalf("video should start at 5 s, at %d", pos)
	}

	if err := e.Seek(12000); err != nil {
		t.Fatal(err)
	}
	if got := fake.Active(2); got != "GM_A" {
		t.Fatalf("crossfade seek should move to GM_A, got %q", got)
	}
	if pos := fake.Position("GM_A"); pos < 12000 {
		t.Fatalf("seek position %d", pos)
	}

	time.Sleep(1200 * time.Millisecond) // let the poller read the position
	if err := e.Clear(); err != nil {
		t.Fatal(err)
	}
	if got := fake.Active(2); got != "Banner Sunday" {
		t.Fatalf("clear should show the default, got %q", got)
	}
	var saved int
	e.Store.Read(func(s *store.State) { saved = s.Positions[2] })
	if saved < 12000 {
		t.Fatalf("position should be remembered for Resume, got %d", saved)
	}
	if e.Status().AssetID != 0 {
		t.Fatal("nothing should be live")
	}
}

func TestExternalSwitchAndVideoEnd(t *testing.T) {
	e, fake := setup(t)
	fake.VideoDurationMs = 1500
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go e.Run(ctx)

	if err := e.Take(2, 0); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) && fake.Active(2) != "Banner Sunday" {
		time.Sleep(100 * time.Millisecond)
	}
	if fake.Active(2) != "Banner Sunday" {
		t.Fatal("video end should return to the default input")
	}

	fake.VideoDurationMs = 60000
	if err := e.Take(1, 0); err != nil {
		t.Fatal(err)
	}
	fake.Switch(2, "Camera 1")
	deadline = time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) && e.Status().AssetID != 0 {
		time.Sleep(100 * time.Millisecond)
	}
	if e.Status().AssetID != 0 {
		t.Fatal("a switch made in vMix should clear the live tile")
	}
}

func TestMissingFile(t *testing.T) {
	e, _ := setup(t)
	if err := e.Take(99, 0); err == nil {
		t.Fatal("expected an error for a file that is not downloaded")
	}
}
