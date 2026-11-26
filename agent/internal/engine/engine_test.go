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
		s.Settings.VideoAudio = true
		s.Settings.AudioFadeMs = 300
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

func TestSeparateTransitionBack(t *testing.T) {
	e, fake := setup(t)
	_ = e.Store.Update(func(s *store.State) { s.Settings.ClearTransition = "AlphaFade" })
	if err := e.Take(1, 0); err != nil {
		t.Fatal(err)
	}
	if err := e.Clear(); err != nil {
		t.Fatal(err)
	}
	last := fake.Calls[len(fake.Calls)-1]
	if fake.Active(2) != "Banner Sunday" || last[:10] != "AlphaFade " {
		t.Fatalf("back to default should use AlphaFade, last call %q", last)
	}
}

func TestVideoSoundFollowsTheMix(t *testing.T) {
	e, fake := setup(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go e.Run(ctx)

	if err := e.Take(2, 0); err != nil {
		t.Fatal(err)
	}
	if !fake.Audio("GM_A") {
		t.Fatal("a live video on Mix 2 should have its sound on")
	}
	if vol, fades := fake.Volume("GM_A"); vol != 100 || len(fades) == 0 || fades[len(fades)-1] != "100,300" {
		t.Fatalf("the sound should fade in to 100 over 300 ms, got %v %v", vol, fades)
	}
	if err := e.Seek(8000); err != nil {
		t.Fatal(err)
	}
	time.Sleep(700 * time.Millisecond) // the old slot fades out, then goes silent
	if !fake.Audio("GM_B") || fake.Audio("GM_A") {
		t.Fatalf("after a crossfade seek the sound should move to GM_B (A=%v B=%v)", fake.Audio("GM_A"), fake.Audio("GM_B"))
	}
	if err := e.Take(1, 0); err != nil { // a photo replaces the video
		t.Fatal(err)
	}
	time.Sleep(700 * time.Millisecond)
	if fake.Audio("GM_A") || fake.Audio("GM_B") {
		t.Fatal("no sound should stay on behind a photo")
	}
	if _, fades := fake.Volume("GM_B"); fades[len(fades)-1] != "0,300" {
		t.Fatalf("the replaced video should fade out, got %v", fades)
	}
	if vol, _ := fake.Volume("GM_B"); vol != 100 {
		t.Fatalf("the slot's volume should be put back after the fade-out, got %v", vol)
	}

	// Left to vMix: Vitrine doesn't touch the audio.
	_ = e.Store.Update(func(s *store.State) { s.Settings.VideoAudio = false })
	if err := e.Take(2, 0); err != nil {
		t.Fatal(err)
	}
	if fake.Audio("GM_A") || fake.Audio("GM_B") {
		t.Fatal("with video_audio off Vitrine must not switch sound on")
	}
}

func TestClipStopsAtMarkOut(t *testing.T) {
	e, fake := setup(t)
	fake.VideoDurationMs = 60000
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go e.Run(ctx)

	// Hold: starts at the mark in, pauses on the mark out.
	if err := e.TakeClip(2, 0, Clip{EntryID: 7, Clip: true, InMs: 5000, OutMs: 7000, AtEnd: "hold"}); err != nil {
		t.Fatal(err)
	}
	if pos := fake.Position("GM_A"); pos < 5000 || pos > 5600 {
		t.Fatalf("a clip should start at its mark in, at %d", pos)
	}
	if st := e.Status(); st.EntryID != 7 || !st.Clip || st.InMs != 5000 || st.OutMs != 7000 {
		t.Fatalf("live should tell which clip is on: %+v", st.Live)
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) && !e.Status().Paused {
		time.Sleep(50 * time.Millisecond)
	}
	if !e.Status().Paused || fake.Active(2) != "GM_A" {
		t.Fatalf("at the mark out a holding clip pauses on screen (paused=%v, mix=%q)", e.Status().Paused, fake.Active(2))
	}
	if pos := fake.Position("GM_A"); pos < 6800 || pos > 7300 {
		t.Fatalf("it should stop close to the mark out (7000), stopped at %d", pos)
	}

	// Seeking stays inside the clip.
	if err := e.Seek(1000); err != nil {
		t.Fatal(err)
	}
	if pos := e.Status().PositionMs; pos < 5000 {
		t.Fatalf("seek before the mark in should go to the mark in, got %d", pos)
	}

	// Fade: back to the default input at the mark out.
	if err := e.TakeClip(2, 0, Clip{EntryID: 8, Clip: true, InMs: 10000, OutMs: 11500, AtEnd: "fade"}); err != nil {
		t.Fatal(err)
	}
	deadline = time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) && fake.Active(2) != "Banner Sunday" {
		time.Sleep(50 * time.Millisecond)
	}
	if fake.Active(2) != "Banner Sunday" || e.Status().AssetID != 0 {
		t.Fatalf("a fading clip should return to the default input (mix=%q)", fake.Active(2))
	}
}
