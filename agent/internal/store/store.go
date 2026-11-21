// Package store keeps the agent's state on disk, so tablets, settings and the
// last manifest survive a restart and work without the server.
package store

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/silvansan/vitrine/agent/internal/model"
)

// Settings are the live settings edited in the companion and on the back tablet.
type Settings struct {
	ServiceID         int64  `json:"service_id"`
	Mix               int    `json:"mix"`
	DefaultInputKey   string `json:"default_input_key"`
	DefaultInputTitle string `json:"default_input_title"`
	Transition        string `json:"transition"`
	TransitionMs      int    `json:"transition_ms"`
	// Transition used to go back to the default input; empty = same as Transition.
	ClearTransition   string `json:"clear_transition"`
	ClearTransitionMs int    `json:"clear_transition_ms"`
	VideoEndReturn    bool   `json:"video_end_return"`
	SeekMode          string `json:"seek_mode"`
	InputA            string `json:"input_a"`
	InputB            string `json:"input_b"`
	// Audio files: keep the default input on the projector (true) or show their card.
	AudioKeepDefault bool   `json:"audio_keep_default"`
	InputAudio       string `json:"input_audio"`
	// Vitrine turns the sound of videos and audio cards on in vMix while they are live
	// (and off afterwards). vMix's own "Audio Auto" only follows the main output, so
	// on another mix the sound would stay off. Off: leave the audio to vMix.
	VideoAudio bool `json:"video_audio"`
	// Fade the sound in and out over this many ms instead of cutting it (0 = cut).
	AudioFadeMs int `json:"audio_fade_ms"`
}

// ScreenOptions decide what a tablet shows. The back tablet sets them for the pulpit.
type ScreenOptions struct {
	ShowTitle       bool `json:"show_title"`
	ShowClear       bool `json:"show_clear"`
	ShowSetSwitcher bool `json:"show_set_switcher"`
	ShowStatus      bool `json:"show_status"`
	ShowFileNames   bool `json:"show_file_names"`
	VideoBasic      bool `json:"video_basic"`
	VideoSeek       bool `json:"video_seek"`
	AllowOverride   bool `json:"allow_override"`
}

// Screen is one paired tablet.
type Screen struct {
	ID           string        `json:"id"`
	Name         string        `json:"name"`
	Role         string        `json:"role"`
	TokenHash    string        `json:"token_hash"`
	Nonce        string        `json:"nonce"`
	CurrentSetID int64         `json:"current_set_id"`
	Theme        string        `json:"theme"`
	Options      ScreenOptions `json:"options"`
	Created      time.Time     `json:"created"`
	LastSeen     time.Time     `json:"last_seen"`
}

// LocalFile records a downloaded asset.
type LocalFile struct {
	Path      string `json:"path"`
	SHA256    string `json:"sha256"`
	Thumb     string `json:"thumb"`
	Filmstrip string `json:"filmstrip"`
}

type State struct {
	Manifest    *model.Manifest      `json:"manifest"`
	Settings    Settings             `json:"settings"`
	Screens     map[string]*Screen   `json:"screens"`
	Positions   map[int64]int        `json:"positions"`
	Files       map[int64]*LocalFile `json:"files"`
	AgentSecret string               `json:"agent_secret"`
	UsedNonces  map[string]time.Time `json:"used_nonces"`
	// Guests the back tablet gave control of the projector, by set id, until a time.
	// They can show their own set's files from their phone (relayed by the server).
	Controls map[int64]time.Time `json:"controls"`
	// API keys for integrations such as Bitfocus Companion.
	IntegrationKeys []*IntegrationKey `json:"integration_keys"`
	// Markers added on tablets that still have to reach the server.
	PendingMarkers []PendingMarker `json:"pending_markers"`
	// Version of the state layout, for one-time upgrades.
	Version int `json:"version"`
}

// IntegrationKey lets another system (e.g. Bitfocus Companion) control the
// agent like the back tablet. Only its hash is stored.
type IntegrationKey struct {
	ID       string    `json:"id"`
	Name     string    `json:"name"`
	Hash     string    `json:"hash"`
	Prefix   string    `json:"prefix"`
	Created  time.Time `json:"created"`
	LastUsed time.Time `json:"last_used"`
}

// PendingMarker is a marker added on a tablet while the server was unreachable.
type PendingMarker struct {
	AssetID int64  `json:"asset_id"`
	TMs     int    `json:"t_ms"`
	Label   string `json:"label"`
	By      string `json:"by"`
}

// DefaultPulpitOptions is the safe set for a tablet on the pulpit.
func DefaultPulpitOptions() ScreenOptions {
	return ScreenOptions{ShowTitle: true, ShowClear: true, VideoBasic: true, VideoSeek: true, AllowOverride: true}
}

// DefaultBackOptions shows everything.
func DefaultBackOptions() ScreenOptions {
	return ScreenOptions{ShowTitle: true, ShowClear: true, ShowSetSwitcher: true, ShowStatus: true,
		ShowFileNames: true, VideoBasic: true, VideoSeek: true, AllowOverride: true}
}

type Store struct {
	mu    sync.Mutex
	path  string
	state State
}

func Open(dataDir string) (*Store, error) {
	s := &Store{path: filepath.Join(dataDir, "state.json")}
	data, err := os.ReadFile(s.path)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	if len(data) > 0 {
		if err := json.Unmarshal(data, &s.state); err != nil {
			// Keep the broken file for inspection and start fresh.
			_ = os.Rename(s.path, s.path+".broken")
			s.state = State{}
		}
	}
	st := &s.state
	if st.Screens == nil {
		st.Screens = map[string]*Screen{}
	}
	if st.Positions == nil {
		st.Positions = map[int64]int{}
	}
	if st.Files == nil {
		st.Files = map[int64]*LocalFile{}
	}
	if st.Controls == nil {
		st.Controls = map[int64]time.Time{}
	}
	if st.UsedNonces == nil {
		st.UsedNonces = map[string]time.Time{}
	}
	if st.AgentSecret == "" {
		buf := make([]byte, 32)
		_, _ = rand.Read(buf)
		st.AgentSecret = hex.EncodeToString(buf)
	}
	if st.Settings.Mix == 0 {
		st.Settings.Mix = 2
	}
	if st.Settings.Transition == "" {
		st.Settings.Transition = "Fade"
	}
	if st.Settings.TransitionMs == 0 {
		st.Settings.TransitionMs = 500
	}
	if st.Settings.SeekMode == "" {
		st.Settings.SeekMode = "crossfade"
	}
	if st.Settings.InputA == "" {
		st.Settings.InputA = "GM_A"
	}
	if st.Settings.InputB == "" {
		st.Settings.InputB = "GM_B"
	}
	if len(data) == 0 {
		st.Settings.VideoEndReturn = true
	}
	if st.Settings.InputAudio == "" {
		st.Settings.InputAudio = "GM_AUDIO"
	}
	if st.Version < 3 {
		st.Settings.AudioKeepDefault = true
	}
	if st.Version < 2 {
		// 0.1.5: free seeking (cue panel, ±10 s, Add marker here) is on for every tablet.
		for _, sc := range st.Screens {
			sc.Options.VideoBasic = true
			sc.Options.VideoSeek = true
		}
	}
	if st.Version < 4 {
		// 0.1.9: videos play their sound (vMix Audio Auto doesn't follow Mix 2+).
		st.Settings.VideoAudio = true
		st.Settings.AudioFadeMs = 800
	}
	if st.Version < 4 {
		st.Version = 4
	}
	return s, s.saveLocked()
}

// Read runs fn with the state locked, for reading.
func (s *Store) Read(fn func(st *State)) {
	s.mu.Lock()
	defer s.mu.Unlock()
	fn(&s.state)
}

// Update runs fn with the state locked and saves it afterwards.
func (s *Store) Update(fn func(st *State)) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	fn(&s.state)
	return s.saveLocked()
}

func (s *Store) saveLocked() error {
	data, err := json.MarshalIndent(&s.state, "", "  ")
	if err != nil {
		return err
	}
	tmp := s.path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, s.path)
}

// Settings returns a copy of the live settings.
func (s *Store) Settings() Settings {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.state.Settings
}

// Manifest returns the last manifest (shared; do not modify).
func (s *Store) Manifest() *model.Manifest {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.state.Manifest
}

// ActiveServiceID returns the chosen service, or the first active one.
func (s *Store) ActiveServiceID() int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	return activeServiceLocked(&s.state)
}

func activeServiceLocked(st *State) int64 {
	m := st.Manifest
	if m == nil || len(m.Services) == 0 {
		return 0
	}
	if m.Service(st.Settings.ServiceID) != nil {
		return st.Settings.ServiceID
	}
	return m.Services[0].ID
}

// ActiveService returns the id of the service tablets show, given a locked state.
func ActiveService(st *State) int64 { return activeServiceLocked(st) }
