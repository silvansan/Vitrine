// Package engine puts media on the projector mix in vMix and keeps track of
// what is live.
//
// Two List inputs (GM_A and GM_B by default) take turns: the next file is
// loaded into the input that is off-air, cued, and then faded onto the mix.
// Jumping inside a live video works the same way (load the same file at the
// new position in the other input, then crossfade), so the audience never sees
// the picture skip.
package engine

import (
	"context"
	"errors"
	"fmt"
	"log"
	"strconv"
	"sync"
	"time"

	"github.com/silvansan/vitrine/agent/internal/config"
	"github.com/silvansan/vitrine/agent/internal/store"
	"github.com/silvansan/vitrine/agent/internal/vmix"
)

// Media is what the engine needs to know about a file.
type Media struct {
	ID         int64
	Path       string
	Kind       string
	DurationMs int
	Name       string
}

// timed reports whether a kind plays over time (video, or audio shown as its card).
func timed(kind string) bool { return kind == "video" || kind == "audio" }

// AudioLive describes an audio file playing while the projector keeps showing
// whatever it shows (normally the default input).
type AudioLive struct {
	AssetID    int64 `json:"asset_id"`
	PendingID  int64 `json:"pending_id"`
	PositionMs int   `json:"position_ms"`
	DurationMs int   `json:"duration_ms"`
	Paused     bool  `json:"paused"`
}

// Live describes what is on the projector mix right now.
type Live struct {
	AssetID    int64  `json:"asset_id"`
	PendingID  int64  `json:"pending_id"`
	Slot       string `json:"slot"`
	Kind       string `json:"kind"`
	PositionMs int    `json:"position_ms"`
	DurationMs int    `json:"duration_ms"`
	Paused     bool   `json:"paused"`
	Jumping    bool   `json:"jumping"`
	Error      string `json:"error"`
}

type Status struct {
	Live
	Audio        AudioLive `json:"audio"`
	VMixOK       bool      `json:"vmix_ok"`
	VMixVersion  string    `json:"vmix_version"`
	VMixError    string    `json:"vmix_error"`
	InputsOK     bool      `json:"inputs_ok"`
	InputsError  string    `json:"inputs_error"`
	DefaultOK    bool      `json:"default_ok"`
	DefaultTitle string    `json:"default_title"`
	Mix          int       `json:"mix"`
	MixActive    string    `json:"mix_active"`
}

type Engine struct {
	VMix     *vmix.Client
	Store    *store.Store
	Cfg      *config.Config
	Resolve  func(id int64) (*Media, bool)
	OnChange func()

	cmd sync.Mutex // one command at a time

	mu      sync.Mutex
	status  Status
	last    *vmix.State
	busy    bool
	ending  int64
	slotGen map[string]int
	// Each slot's own volume (as set in vMix), restored after a sound fade-out.
	slotVolume map[string]float64
	// Bumped by every PlayAudio, so a fade-out doesn't stop the next file.
	audioGen  int
	used      []int64
	errorTill time.Time
}

// transitionList is what the companion offers, in vMix's function names. Only
// effects that take an Input and a Mix are listed: vMix's Transition1-4 buttons
// and stingers act on the main output and would switch Mix 1.
// AlphaFade fades correctly onto inputs with transparency.
var transitionList = []string{
	"Fade", "AlphaFade", "Cut", "Merge", "Wipe", "WipeReverse", "Zoom", "CrossZoom", "Slide", "SlideReverse",
	"Fly", "FlyRotate", "Cube", "CubeZoom", "VerticalWipe", "VerticalWipeReverse", "VerticalSlide",
	"VerticalSlideReverse",
}

var transitions = func() map[string]bool {
	out := map[string]bool{}
	for _, name := range transitionList {
		out[name] = true
	}
	return out
}()

// Transitions lists the transition names the companion offers.
func Transitions() []string { return transitionList }

func (e *Engine) Status() Status {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.status
}

// LastState returns the last vMix state read (may be nil).
func (e *Engine) LastState() *vmix.State {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.last
}

// TakeUsed returns and clears the assets taken live since the last call.
func (e *Engine) TakeUsed() []int64 {
	e.mu.Lock()
	defer e.mu.Unlock()
	out := e.used
	e.used = nil
	return out
}

// IsLive reports whether anything is on the projector.
func (e *Engine) IsLive() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.status.AssetID != 0 || e.status.PendingID != 0 || e.status.Audio.AssetID != 0
}

// InUse returns assets currently loaded or live, which must not be deleted.
func (e *Engine) InUse() map[int64]bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return map[int64]bool{e.status.AssetID: true, e.status.PendingID: true, e.status.Audio.AssetID: true}
}

func (e *Engine) changed() {
	if e.OnChange != nil {
		e.OnChange()
	}
}

func (e *Engine) setError(err error) {
	e.mu.Lock()
	e.status.Error = err.Error()
	e.status.PendingID = 0
	e.errorTill = time.Now().Add(8 * time.Second)
	e.mu.Unlock()
	log.Printf("engine: %v", err)
	e.changed()
}

func (e *Engine) mixParams(s store.Settings, p map[string]string) map[string]string {
	if s.Mix > 1 {
		p["Mix"] = strconv.Itoa(s.Mix + e.Cfg.MixParamOffset)
	}
	return p
}

func (e *Engine) transition(ctx context.Context, s store.Settings, input string, durationMs int) error {
	return e.transitionWith(ctx, s, s.Transition, input, durationMs)
}

// transitionWith runs a named vMix transition onto the projector mix.
func (e *Engine) transitionWith(ctx context.Context, s store.Settings, fn, input string, durationMs int) error {
	if !transitions[fn] {
		fn = "Fade"
	}
	params := map[string]string{"Input": input}
	if fn != "Cut" {
		params["Duration"] = strconv.Itoa(durationMs)
	}
	return e.VMix.Call(ctx, fn, e.mixParams(s, params))
}

func other(slot string) string {
	if slot == "A" {
		return "B"
	}
	return "A"
}

func slotName(s store.Settings, slot string) string {
	if slot == "B" {
		return s.InputB
	}
	return s.InputA
}

// slots finds the two List inputs, creating them if they are missing.
func (e *Engine) slots(ctx context.Context, s store.Settings) (*vmix.State, error) {
	st, err := e.VMix.State(ctx)
	if err != nil {
		return nil, fmt.Errorf("vMix is not reachable: %w", err)
	}
	for _, name := range []string{s.InputA, s.InputB} {
		if st.InputByName(name) != nil {
			continue
		}
		if err := e.createListInput(ctx, st, name); err != nil {
			return nil, err
		}
		st, err = e.VMix.State(ctx)
		if err != nil {
			return nil, err
		}
	}
	return st, nil
}

func (e *Engine) createListInput(ctx context.Context, st *vmix.State, name string) error {
	maxNumber := 0
	for _, in := range st.Inputs {
		if in.Number > maxNumber {
			maxNumber = in.Number
		}
	}
	_ = e.VMix.Call(ctx, "AddInput", map[string]string{"Value": "VideoList|"})
	for i := 0; i < 15; i++ {
		time.Sleep(200 * time.Millisecond)
		now, err := e.VMix.State(ctx)
		if err != nil {
			continue
		}
		if in := now.InputByNumber(maxNumber + 1); in != nil {
			if err := e.VMix.Call(ctx, "SetInputName", map[string]string{"Input": in.Key, "Value": name}); err != nil {
				return err
			}
			log.Printf("created vMix List input %s", name)
			return nil
		}
	}
	return fmt.Errorf("couldn't create the vMix input %q; add a List input with that name in vMix and save the preset", name)
}

func (e *Engine) defaultInput(st *vmix.State, s store.Settings) *vmix.Input {
	if in := st.InputByKey(s.DefaultInputKey); in != nil {
		return in
	}
	return st.InputByName(s.DefaultInputTitle)
}

func (e *Engine) waitActive(ctx context.Context, mix, number int) bool {
	deadline := time.Now().Add(4 * time.Second)
	for time.Now().Before(deadline) {
		st, err := e.VMix.State(ctx)
		if err == nil && st.Active[mix] == number {
			e.mu.Lock()
			e.last = st
			e.mu.Unlock()
			return true
		}
		time.Sleep(120 * time.Millisecond)
	}
	return false
}

func (e *Engine) rememberPosition(live Live) {
	if live.AssetID == 0 || !timed(live.Kind) {
		return
	}
	_ = e.Store.Update(func(st *store.State) {
		if live.PositionMs > 1500 && (live.DurationMs == 0 || live.PositionMs < live.DurationMs-2000) {
			st.Positions[live.AssetID] = live.PositionMs
		} else {
			delete(st.Positions, live.AssetID)
		}
	})
}

func (e *Engine) begin() (Live, store.Settings) {
	e.cmd.Lock()
	e.mu.Lock()
	e.busy = true
	live := e.status.Live
	e.mu.Unlock()
	return live, e.Store.Settings()
}

func (e *Engine) end() {
	e.mu.Lock()
	e.busy = false
	e.mu.Unlock()
	e.cmd.Unlock()
}

// load puts a file into the given slot, cued at startMs.
func (e *Engine) load(ctx context.Context, st *vmix.State, s store.Settings, slot string, media *Media, startMs int) (*vmix.Input, error) {
	input := st.InputByName(slotName(s, slot))
	if input == nil {
		return nil, fmt.Errorf("vMix input %s is missing", slotName(s, slot))
	}
	e.mu.Lock()
	if e.slotGen == nil {
		e.slotGen = map[string]int{}
	}
	e.slotGen[slot]++
	e.mu.Unlock()
	steps := []struct {
		fn     string
		params map[string]string
	}{
		{"ListRemoveAll", map[string]string{"Input": input.Key}},
		{"ListAdd", map[string]string{"Input": input.Key, "Value": media.Path}},
		{"SelectIndex", map[string]string{"Input": input.Key, "Value": "1"}},
	}
	for _, step := range steps {
		if err := e.VMix.Call(ctx, step.fn, step.params); err != nil {
			return nil, err
		}
	}
	// vMix opens the file asynchronously.
	time.Sleep(250 * time.Millisecond)
	if timed(media.Kind) {
		if err := e.VMix.Call(ctx, "SetPosition", map[string]string{"Input": input.Key, "Value": strconv.Itoa(startMs)}); err != nil {
			return nil, err
		}
		if err := e.VMix.Call(ctx, "Play", map[string]string{"Input": input.Key}); err != nil {
			return nil, err
		}
	}
	return input, nil
}

// pauseLater pauses a slot once it has faded out, unless something new was
// loaded into it in the meantime.
func (e *Engine) pauseLater(s store.Settings, slot string, delayMs int) {
	e.mu.Lock()
	gen := e.slotGen[slot]
	e.mu.Unlock()
	go func() {
		time.Sleep(time.Duration(delayMs+200) * time.Millisecond)
		e.mu.Lock()
		reused := e.slotGen[slot] != gen
		onAir := e.status.AssetID != 0 && e.status.Slot == slot
		e.mu.Unlock()
		if reused || onAir {
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = e.VMix.Call(ctx, "Pause", map[string]string{"Input": slotName(s, slot)})
		e.silence(ctx, s, slotName(s, slot))
	}()
}

// Sound of the slots (unless audio is left to vMix). vMix's "Audio Auto" only
// switches sound for the main output (Mix 1), so a video on Mix 2+ would otherwise
// play silently. The sound fades in and out over AudioFadeMs instead of cutting;
// each slot's own volume (as set in vMix) is kept and restored after a fade-out.

func (e *Engine) volumeOf(name string) float64 {
	e.mu.Lock()
	defer e.mu.Unlock()
	if v, ok := e.slotVolume[name]; ok && v > 0 {
		return v
	}
	return 100
}

func (e *Engine) rememberVolume(name string, v float64) {
	if v <= 0 {
		return
	}
	e.mu.Lock()
	if e.slotVolume == nil {
		e.slotVolume = map[string]float64{}
	}
	e.slotVolume[name] = v
	e.mu.Unlock()
}

func volumeText(v float64) string { return strconv.FormatFloat(v, 'f', -1, 64) }

// soundIn turns a slot's audio on, fading up to its volume over fadeMs.
func (e *Engine) soundIn(ctx context.Context, s store.Settings, in *vmix.Input, fadeMs int) {
	if !s.VideoAudio || in == nil {
		return
	}
	name := in.Name()
	e.rememberVolume(name, in.Volume)
	vol := e.volumeOf(name)
	_ = e.VMix.Call(ctx, "AudioAutoOff", map[string]string{"Input": in.Key})
	if fadeMs > 0 {
		_ = e.VMix.Call(ctx, "SetVolume", map[string]string{"Input": in.Key, "Value": "0"})
	}
	if err := e.VMix.Call(ctx, "AudioOn", map[string]string{"Input": in.Key}); err != nil {
		log.Printf("vMix AudioOn %s: %v", name, err)
	}
	if fadeMs > 0 {
		_ = e.VMix.Call(ctx, "SetVolumeFade", map[string]string{"Input": in.Key, "Value": volumeText(vol) + "," + strconv.Itoa(fadeMs)})
	}
}

// soundOut starts fading a slot's audio down; silence() finishes once the slot is off the mix.
func (e *Engine) soundOut(ctx context.Context, s store.Settings, st *vmix.State, name string, fadeMs int) {
	if !s.VideoAudio {
		return
	}
	if st != nil {
		if in := st.InputByName(name); in != nil {
			e.rememberVolume(name, in.Volume)
		}
	}
	if fadeMs > 0 {
		_ = e.VMix.Call(ctx, "SetVolumeFade", map[string]string{"Input": name, "Value": "0," + strconv.Itoa(fadeMs)})
		return
	}
	_ = e.VMix.Call(ctx, "AudioOff", map[string]string{"Input": name})
}

// silence turns a faded-out slot's audio off and puts its volume back for next time.
func (e *Engine) silence(ctx context.Context, s store.Settings, name string) {
	if !s.VideoAudio {
		return
	}
	_ = e.VMix.Call(ctx, "AudioOff", map[string]string{"Input": name})
	if s.AudioFadeMs > 0 {
		_ = e.VMix.Call(ctx, "SetVolume", map[string]string{"Input": name, "Value": volumeText(e.volumeOf(name))})
	}
}

// outMs is how long a slot needs before it can be paused: the picture transition or the sound fade.
func outMs(s store.Settings, transitionMs int) int {
	if s.VideoAudio && s.AudioFadeMs > transitionMs {
		return s.AudioFadeMs
	}
	return transitionMs
}

// Take puts an asset on the projector mix, starting at startMs (videos).
func (e *Engine) Take(assetID int64, startMs int) error {
	media, ok := e.Resolve(assetID)
	if !ok {
		err := errors.New("this file is not on the vMix PC yet; it is still downloading")
		e.setError(err)
		return err
	}
	if media.Kind == "audio" && e.Store.Settings().AudioKeepDefault {
		return e.PlayAudio(media, startMs)
	}
	prev, s := e.begin()
	defer e.end()
	ctx, cancel := context.WithTimeout(context.Background(), 12*time.Second)
	defer cancel()

	e.mu.Lock()
	e.status.PendingID = assetID
	e.status.Error = ""
	e.mu.Unlock()
	e.changed()

	st, err := e.slots(ctx, s)
	if err != nil {
		e.setError(err)
		return err
	}
	target := "A"
	if prev.AssetID != 0 {
		target = other(prev.Slot)
	} else if a := st.InputByName(s.InputA); a != nil && st.Active[s.Mix] == a.Number {
		target = "B"
	}
	if startMs < 0 || (media.DurationMs > 0 && startMs >= media.DurationMs) {
		startMs = 0
	}
	input, err := e.load(ctx, st, s, target, media, startMs)
	if err != nil {
		e.setError(err)
		return err
	}
	if err := e.transition(ctx, s, input.Key, s.TransitionMs); err != nil {
		e.setError(err)
		return err
	}
	// Sound with the picture: the new video fades in, the one it replaces fades out.
	if timed(media.Kind) {
		e.soundIn(ctx, s, input, s.AudioFadeMs)
	}
	if prev.AssetID != 0 && timed(prev.Kind) {
		e.soundOut(ctx, s, st, slotName(s, prev.Slot), s.AudioFadeMs)
	}
	if !e.waitActive(ctx, s.Mix, input.Number) {
		err := fmt.Errorf("vMix didn't switch Mix %d; check the projector mix in the companion", s.Mix)
		e.setError(err)
		return err
	}
	e.rememberPosition(prev)
	e.mu.Lock()
	e.ending = 0
	e.status.Live = Live{AssetID: assetID, Slot: target, Kind: media.Kind, PositionMs: startMs, DurationMs: media.DurationMs}
	e.used = append(e.used, assetID)
	e.mu.Unlock()
	if prev.AssetID != 0 && timed(prev.Kind) {
		e.pauseLater(s, prev.Slot, outMs(s, s.TransitionMs))
	}
	e.changed()
	return nil
}

// Clear returns the projector mix to the default input.
func (e *Engine) Clear() error {
	prev, s := e.begin()
	defer e.end()
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	st, err := e.VMix.State(ctx)
	if err != nil {
		e.setError(fmt.Errorf("vMix is not reachable: %w", err))
		return err
	}
	def := e.defaultInput(st, s)
	if def == nil {
		err := errors.New("no default input set; choose it in the companion on the vMix PC")
		e.setError(err)
		return err
	}
	backFn, backMs := s.Transition, s.TransitionMs
	if s.ClearTransition != "" {
		backFn = s.ClearTransition
		if s.ClearTransitionMs > 0 {
			backMs = s.ClearTransitionMs
		}
	}
	if err := e.transitionWith(ctx, s, backFn, def.Key, backMs); err != nil {
		e.setError(err)
		return err
	}
	if timed(prev.Kind) {
		e.soundOut(ctx, s, st, slotName(s, prev.Slot), s.AudioFadeMs)
	}
	e.waitActive(ctx, s.Mix, def.Number)
	e.rememberPosition(prev)
	e.mu.Lock()
	e.status.Live = Live{}
	e.mu.Unlock()
	if timed(prev.Kind) {
		e.pauseLater(s, prev.Slot, outMs(s, backMs))
	}
	e.changed()
	return nil
}

// Pause and Play control the live video.
func (e *Engine) Pause() error { return e.playPause("Pause", true) }
func (e *Engine) Play() error  { return e.playPause("Play", false) }

func (e *Engine) playPause(fn string, paused bool) error {
	live, s := e.begin()
	defer e.end()
	if live.AssetID == 0 || !timed(live.Kind) {
		return e.audioPlayPause(s, fn, paused)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()
	if err := e.VMix.Call(ctx, fn, map[string]string{"Input": slotName(s, live.Slot)}); err != nil {
		e.setError(err)
		return err
	}
	e.mu.Lock()
	e.status.Paused = paused
	e.mu.Unlock()
	e.changed()
	return nil
}

// Seek jumps inside the live video. In crossfade mode the jump happens in the
// off-air input and is faded in; in direct mode vMix jumps in place.
func (e *Engine) Seek(positionMs int) error {
	live, s := e.begin()
	defer e.end()
	if live.AssetID == 0 || !timed(live.Kind) {
		return e.audioSeek(s, positionMs)
	}
	media, ok := e.Resolve(live.AssetID)
	if !ok {
		return errors.New("file missing")
	}
	if positionMs < 0 {
		positionMs = 0
	}
	if media.DurationMs > 0 && positionMs > media.DurationMs-500 {
		positionMs = media.DurationMs - 500
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	if s.SeekMode == "direct" {
		if err := e.VMix.Call(ctx, "SetPosition", map[string]string{"Input": slotName(s, live.Slot), "Value": strconv.Itoa(positionMs)}); err != nil {
			e.setError(err)
			return err
		}
		e.mu.Lock()
		e.status.PositionMs = positionMs
		e.mu.Unlock()
		e.changed()
		return nil
	}

	e.mu.Lock()
	e.status.Jumping = true
	e.mu.Unlock()
	e.changed()
	defer func() {
		e.mu.Lock()
		e.status.Jumping = false
		e.mu.Unlock()
		e.changed()
	}()

	st, err := e.slots(ctx, s)
	if err != nil {
		e.setError(err)
		return err
	}
	target := other(live.Slot)
	input, err := e.load(ctx, st, s, target, media, positionMs)
	if err != nil {
		e.setError(err)
		return err
	}
	if live.Paused {
		_ = e.VMix.Call(ctx, "Pause", map[string]string{"Input": input.Key})
	}
	jumpMs := s.TransitionMs
	if jumpMs > 400 || jumpMs <= 0 {
		jumpMs = 400
	}
	params := e.mixParams(s, map[string]string{"Input": input.Key, "Duration": strconv.Itoa(jumpMs)})
	if err := e.VMix.Call(ctx, "Fade", params); err != nil {
		e.setError(err)
		return err
	}
	// The sound crossfades with the picture.
	e.soundIn(ctx, s, input, jumpMs)
	e.soundOut(ctx, s, st, slotName(s, live.Slot), jumpMs)
	if !e.waitActive(ctx, s.Mix, input.Number) {
		err := errors.New("vMix didn't take the jump")
		e.setError(err)
		return err
	}
	e.mu.Lock()
	e.status.Slot = target
	e.status.PositionMs = positionMs
	e.mu.Unlock()
	e.pauseLater(s, live.Slot, jumpMs)
	return nil
}

// ShowDefault puts the default input on the mix (companion test button).
func (e *Engine) ShowDefault() error { return e.Clear() }

// Run polls vMix until ctx ends.
func (e *Engine) Run(ctx context.Context) {
	lastPush := time.Time{}
	for ctx.Err() == nil {
		interval := time.Second
		e.mu.Lock()
		if (e.status.AssetID != 0 && timed(e.status.Kind)) || e.status.Audio.AssetID != 0 {
			interval = 300 * time.Millisecond
		}
		e.mu.Unlock()
		select {
		case <-ctx.Done():
			return
		case <-time.After(interval):
		}
		changed := e.poll(ctx)
		if changed || time.Since(lastPush) > 5*time.Second || e.IsVideoPlaying() {
			lastPush = time.Now()
			e.changed()
		}
	}
}

// IsVideoPlaying reports whether a live video is running (progress updates).
func (e *Engine) IsVideoPlaying() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.status.Audio.AssetID != 0 && !e.status.Audio.Paused {
		return true
	}
	return e.status.AssetID != 0 && timed(e.status.Kind) && !e.status.Paused
}

func (e *Engine) poll(ctx context.Context) bool {
	pollCtx, cancel := context.WithTimeout(ctx, 2500*time.Millisecond)
	defer cancel()
	s := e.Store.Settings()
	st, err := e.VMix.State(pollCtx)

	e.mu.Lock()
	before := e.status
	if err != nil {
		e.status.VMixOK = false
		e.status.VMixError = err.Error()
		e.mu.Unlock()
		return before.VMixOK
	}
	e.last = st
	e.status.VMixOK = true
	e.status.VMixError = ""
	e.status.VMixVersion = st.Version
	e.status.Mix = s.Mix
	a, b := st.InputByName(s.InputA), st.InputByName(s.InputB)
	e.status.InputsOK = a != nil && b != nil
	if e.status.InputsOK {
		e.status.InputsError = ""
	} else {
		e.status.InputsError = fmt.Sprintf("vMix inputs %s / %s not found yet (they are created on the first take)", s.InputA, s.InputB)
	}
	def := e.defaultInput(st, s)
	e.status.DefaultOK = def != nil
	e.status.DefaultTitle = s.DefaultInputTitle
	if active := st.InputByNumber(st.Active[s.Mix]); active != nil {
		e.status.MixActive = active.Name()
	} else {
		e.status.MixActive = ""
	}
	if e.status.Error != "" && time.Now().After(e.errorTill) {
		e.status.Error = ""
	}

	var ended, external bool
	live := e.status.Live
	if live.AssetID != 0 && !e.busy {
		slot := st.InputByName(slotName(s, live.Slot))
		if slot == nil || st.Active[s.Mix] != slot.Number {
			external = true
		} else if timed(live.Kind) {
			e.status.PositionMs = slot.Position
			if slot.Duration > 0 {
				e.status.DurationMs = slot.Duration
			}
			e.status.Paused = slot.State == "Paused"
			if e.status.DurationMs > 0 && slot.Position >= e.status.DurationMs-350 {
				ended = true
			}
		}
	}
	if external {
		// Someone switched the mix in vMix itself: follow it.
		e.status.Live = Live{}
	}
	audioEnded := false
	audio := e.status.Audio
	if audio.AssetID != 0 && !e.busy {
		if in := st.InputByName(s.InputAudio); in != nil {
			e.status.Audio.PositionMs = in.Position
			if in.Duration > 0 {
				e.status.Audio.DurationMs = in.Duration
			}
			e.status.Audio.Paused = in.State == "Paused"
			if e.status.Audio.DurationMs > 0 && in.Position >= e.status.Audio.DurationMs-350 {
				audioEnded = true
			}
		} else {
			e.status.Audio = AudioLive{}
		}
	}
	after := e.status
	e.mu.Unlock()
	if audioEnded {
		_ = e.Store.Update(func(st *store.State) { delete(st.Positions, audio.AssetID) })
		go func() { _ = e.StopAudio() }()
	}

	if external {
		e.rememberPosition(live)
	}
	if ended {
		e.mu.Lock()
		first := e.ending != live.AssetID
		e.ending = live.AssetID
		e.mu.Unlock()
		if !first {
			return false
		}
		_ = e.Store.Update(func(st *store.State) { delete(st.Positions, live.AssetID) })
		if s.VideoEndReturn {
			go func() { _ = e.Clear() }()
		} else {
			e.mu.Lock()
			e.status.Paused = true
			e.mu.Unlock()
		}
	}
	return before.VMixOK != after.VMixOK || before.AssetID != after.AssetID || before.Paused != after.Paused ||
		before.Audio.AssetID != after.Audio.AssetID || before.Audio.Paused != after.Audio.Paused ||
		before.InputsOK != after.InputsOK || before.DefaultOK != after.DefaultOK || before.MixActive != after.MixActive ||
		before.Error != after.Error
}

// ---------- audio that plays while the projector keeps the default input ----------

// audioInput finds the audio List input, creating it if needed.
func (e *Engine) audioInput(ctx context.Context, s store.Settings) (*vmix.Input, error) {
	st, err := e.VMix.State(ctx)
	if err != nil {
		return nil, fmt.Errorf("vMix is not reachable: %w", err)
	}
	if in := st.InputByName(s.InputAudio); in != nil {
		return in, nil
	}
	if err := e.createListInput(ctx, st, s.InputAudio); err != nil {
		return nil, err
	}
	st, err = e.VMix.State(ctx)
	if err != nil {
		return nil, err
	}
	if in := st.InputByName(s.InputAudio); in != nil {
		return in, nil
	}
	return nil, fmt.Errorf("vMix input %s is missing", s.InputAudio)
}

// PlayAudio plays an audio file in its own input without touching the projector mix.
func (e *Engine) PlayAudio(media *Media, startMs int) error {
	_, s := e.begin()
	defer e.end()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	e.mu.Lock()
	prevAudio := e.status.Audio
	e.status.Audio.PendingID = media.ID
	e.status.Error = ""
	e.mu.Unlock()
	e.changed()

	in, err := e.audioInput(ctx, s)
	if err != nil {
		e.audioFailed(err)
		return err
	}
	e.rememberAudio(prevAudio)
	if startMs < 0 || (media.DurationMs > 0 && startMs >= media.DurationMs) {
		startMs = 0
	}
	steps := []struct {
		fn     string
		params map[string]string
	}{
		{"ListRemoveAll", map[string]string{"Input": in.Key}},
		{"ListAdd", map[string]string{"Input": in.Key, "Value": media.Path}},
		{"SelectIndex", map[string]string{"Input": in.Key, "Value": "1"}},
	}
	for _, step := range steps {
		if err := e.VMix.Call(ctx, step.fn, step.params); err != nil {
			e.audioFailed(err)
			return err
		}
	}
	time.Sleep(250 * time.Millisecond)
	e.mu.Lock()
	e.audioGen++
	e.mu.Unlock()
	name := in.Name()
	e.rememberVolume(name, in.Volume)
	fade := s.AudioFadeMs
	// Heard although the input isn't on the output: automatic audio off, audio on,
	// fading up to the input's own volume.
	steps2 := []struct {
		fn     string
		params map[string]string
	}{
		{"SetPosition", map[string]string{"Input": in.Key, "Value": strconv.Itoa(startMs)}},
		{"AudioAutoOff", map[string]string{"Input": in.Key}},
	}
	if fade > 0 {
		steps2 = append(steps2, struct {
			fn     string
			params map[string]string
		}{"SetVolume", map[string]string{"Input": in.Key, "Value": "0"}})
	}
	steps2 = append(steps2, []struct {
		fn     string
		params map[string]string
	}{
		{"AudioOn", map[string]string{"Input": in.Key}},
		{"Play", map[string]string{"Input": in.Key}},
	}...)
	if fade > 0 {
		steps2 = append(steps2, struct {
			fn     string
			params map[string]string
		}{"SetVolumeFade", map[string]string{"Input": in.Key, "Value": volumeText(e.volumeOf(name)) + "," + strconv.Itoa(fade)}})
	}
	for _, step := range steps2 {
		if err := e.VMix.Call(ctx, step.fn, step.params); err != nil {
			e.audioFailed(err)
			return err
		}
	}
	e.mu.Lock()
	e.status.Audio = AudioLive{AssetID: media.ID, PositionMs: startMs, DurationMs: media.DurationMs}
	e.used = append(e.used, media.ID)
	e.mu.Unlock()
	e.changed()
	return nil
}

func (e *Engine) audioFailed(err error) {
	e.mu.Lock()
	e.status.Audio.PendingID = 0
	e.mu.Unlock()
	e.setError(err)
}

func (e *Engine) rememberAudio(a AudioLive) {
	if a.AssetID == 0 {
		return
	}
	e.rememberPosition(Live{AssetID: a.AssetID, Kind: "audio", PositionMs: a.PositionMs, DurationMs: a.DurationMs})
}

// StopAudio stops the audio file (fades nothing on the projector).
func (e *Engine) StopAudio() error {
	_, s := e.begin()
	defer e.end()
	e.mu.Lock()
	audio := e.status.Audio
	e.mu.Unlock()
	if audio.AssetID == 0 {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if s.AudioFadeMs > 0 {
		// Fade out, then pause and put the volume back, unless another file started meanwhile.
		if st, err := e.VMix.State(ctx); err == nil {
			if in := st.InputByName(s.InputAudio); in != nil {
				e.rememberVolume(s.InputAudio, in.Volume)
			}
		}
		_ = e.VMix.Call(ctx, "SetVolumeFade", map[string]string{"Input": s.InputAudio, "Value": "0," + strconv.Itoa(s.AudioFadeMs)})
		e.mu.Lock()
		gen := e.audioGen
		e.mu.Unlock()
		go func() {
			time.Sleep(time.Duration(s.AudioFadeMs+150) * time.Millisecond)
			e.mu.Lock()
			again := e.audioGen != gen
			e.mu.Unlock()
			if again {
				return
			}
			c, done := context.WithTimeout(context.Background(), 3*time.Second)
			defer done()
			_ = e.VMix.Call(c, "Pause", map[string]string{"Input": s.InputAudio})
			_ = e.VMix.Call(c, "AudioOff", map[string]string{"Input": s.InputAudio})
			_ = e.VMix.Call(c, "SetVolume", map[string]string{"Input": s.InputAudio, "Value": volumeText(e.volumeOf(s.InputAudio))})
		}()
	} else {
		_ = e.VMix.Call(ctx, "Pause", map[string]string{"Input": s.InputAudio})
		_ = e.VMix.Call(ctx, "AudioOff", map[string]string{"Input": s.InputAudio})
	}
	e.rememberAudio(audio)
	e.mu.Lock()
	e.status.Audio = AudioLive{}
	e.mu.Unlock()
	e.changed()
	return nil
}

func (e *Engine) audioPlayPause(s store.Settings, fn string, paused bool) error {
	e.mu.Lock()
	audio := e.status.Audio
	e.mu.Unlock()
	if audio.AssetID == 0 {
		return errors.New("nothing is playing")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()
	if err := e.VMix.Call(ctx, fn, map[string]string{"Input": s.InputAudio}); err != nil {
		e.setError(err)
		return err
	}
	e.mu.Lock()
	e.status.Audio.Paused = paused
	e.mu.Unlock()
	e.changed()
	return nil
}

func (e *Engine) audioSeek(s store.Settings, positionMs int) error {
	e.mu.Lock()
	audio := e.status.Audio
	e.mu.Unlock()
	if audio.AssetID == 0 {
		return errors.New("nothing is playing")
	}
	if positionMs < 0 {
		positionMs = 0
	}
	if audio.DurationMs > 0 && positionMs > audio.DurationMs-500 {
		positionMs = audio.DurationMs - 500
	}
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()
	if err := e.VMix.Call(ctx, "SetPosition", map[string]string{"Input": s.InputAudio, "Value": strconv.Itoa(positionMs)}); err != nil {
		e.setError(err)
		return err
	}
	e.mu.Lock()
	e.status.Audio.PositionMs = positionMs
	e.mu.Unlock()
	e.changed()
	return nil
}
