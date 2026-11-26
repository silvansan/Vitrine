package web

import (
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"time"

	"github.com/silvansan/vitrine/agent/internal/model"
	"github.com/silvansan/vitrine/agent/internal/store"
)

// Guests controlling the projector from their own phone.
//
// The back tablet gives a guest control (by their set in the current event) until
// midnight or until it takes it back. The guest's page on the server sends commands;
// the server relays them over the agent's WebSocket; the agent checks the grant and
// that the file belongs to that guest's set before doing anything.

// accessRequest: a guest asked (from their phone) to control the screen.
type accessRequest struct {
	SetID int64     `json:"set_id"`
	Guest string    `json:"guest"`
	By    string    `json:"by"`
	At    time.Time `json:"at"`
}

const requestTTL = 10 * time.Minute

func (s *Server) requestsView() []accessRequest {
	s.reqMu.Lock()
	defer s.reqMu.Unlock()
	out := []accessRequest{}
	for id, r := range s.requests {
		if time.Since(r.At) > requestTTL {
			delete(s.requests, id)
			continue
		}
		out = append(out, r)
	}
	return out
}

func (s *Server) dropRequest(setID int64) {
	s.reqMu.Lock()
	delete(s.requests, setID)
	s.reqMu.Unlock()
}

// RemoteCommand is what the server relays from a guest's page.
type RemoteCommand struct {
	Type       string `json:"type"`
	Action     string `json:"action"` // take, clear, pause, play, seek, stop_audio
	SetID      int64  `json:"set_id"`
	AssetID    int64  `json:"asset_id"`
	EntryID    int64  `json:"entry_id"` // the tile in their set (a clip has marks)
	StartMs    int    `json:"start_ms"`
	PositionMs int    `json:"position_ms"`
	By         string `json:"by"`
}

// activeControls returns the sets that have control now (expired grants are dropped).
func activeControls(st *store.State) map[int64]time.Time {
	out := map[int64]time.Time{}
	now := time.Now()
	for id, until := range st.Controls {
		if until.After(now) {
			out[id] = until
		}
	}
	return out
}

type controlView struct {
	SetID int64     `json:"set_id"`
	Guest string    `json:"guest"`
	Until time.Time `json:"until"`
}

func (s *Server) controlsView() []controlView {
	out := []controlView{}
	m := s.Store.Manifest()
	s.Store.Read(func(st *store.State) {
		for id, until := range activeControls(st) {
			guest := ""
			if set := setByID(m, id); set != nil {
				guest = set.Guest
			}
			out = append(out, controlView{SetID: id, Guest: guest, Until: until})
		}
	})
	return out
}

func setByID(m *model.Manifest, id int64) *model.Set {
	if m == nil {
		return nil
	}
	for i := range m.Sets {
		if m.Sets[i].ID == id {
			return &m.Sets[i]
		}
	}
	return nil
}

func endOfDay(now time.Time) time.Time {
	y, mo, d := now.Date()
	return time.Date(y, mo, d, 23, 59, 59, 0, now.Location())
}

// handleControl: the back tablet gives or takes back a guest's control.
func (s *Server) handleControl(w http.ResponseWriter, r *http.Request, sc *store.Screen) {
	if sc.Role != "back" {
		writeError(w, http.StatusForbidden, "only the back tablet can give control")
		return
	}
	var body struct {
		SetID int64 `json:"set_id"`
		On    bool  `json:"on"`
	}
	if err := readJSON(r, &body); err != nil || body.SetID == 0 {
		writeError(w, http.StatusBadRequest, "bad request")
		return
	}
	set := setByID(s.Store.Manifest(), body.SetID)
	if set == nil {
		writeError(w, http.StatusNotFound, "unknown guest")
		return
	}
	hadControl := false
	_ = s.Store.Update(func(st *store.State) {
		_, hadControl = activeControls(st)[body.SetID]
		if body.On {
			st.Controls[body.SetID] = endOfDay(time.Now())
		} else {
			delete(st.Controls, body.SetID)
		}
	})
	s.dropRequest(body.SetID)
	if body.On {
		s.Notice(fmt.Sprintf("%s can now show their files from their phone.", set.Guest))
	} else if hadControl {
		s.Notice(fmt.Sprintf("%s no longer controls the screen.", set.Guest))
	} else {
		s.Notice(fmt.Sprintf("Declined %s's request.", set.Guest))
	}
	log.Printf("control for %s (set %d): %v", set.Guest, body.SetID, body.On)
	s.BroadcastState()
	if s.OnControlsChanged != nil {
		s.OnControlsChanged()
	}
	writeJSON(w, map[string]bool{"ok": true})
}

// Notice shows a short message on every tablet.
func (s *Server) Notice(text string) {
	if s.hub != nil {
		s.hub.Broadcast(map[string]string{"type": "notice", "text": text})
	}
}

// ExecuteRemote runs a guest's command if they have control and the file is theirs.
func (s *Server) ExecuteRemote(raw json.RawMessage) error {
	var cmd RemoteCommand
	if err := json.Unmarshal(raw, &cmd); err != nil {
		return err
	}
	m := s.Store.Manifest()
	var allowed bool
	var inActive bool
	s.Store.Read(func(st *store.State) {
		_, allowed = activeControls(st)[cmd.SetID]
		for _, set := range m.SetsFor(store.ActiveService(st)) {
			if set.ID == cmd.SetID {
				inActive = true
			}
		}
	})
	set := setByID(m, cmd.SetID)
	if set == nil || !inActive {
		return errors.New("you aren't in the event the tablets show right now")
	}
	if !allowed && cmd.Action != "request_access" && cmd.Action != "cancel_request" {
		return errors.New("no control: the production desk hasn't given it, or took it back")
	}
	inSet := func(id int64) bool {
		for _, item := range set.Items {
			if item == id {
				return true
			}
		}
		return false
	}
	who := cmd.By
	if who == "" {
		who = set.Guest
	}
	live := s.Engine.Status()
	liveIsTheirs := (live.AssetID != 0 && inSet(live.AssetID)) || (live.Audio.AssetID != 0 && inSet(live.Audio.AssetID))
	name := func(id int64) string {
		if a := m.Asset(id); a != nil {
			return a.Name
		}
		return "a file"
	}
	switch cmd.Action {
	case "request_access":
		if allowed {
			return nil
		}
		s.reqMu.Lock()
		if s.requests == nil {
			s.requests = map[int64]accessRequest{}
		}
		s.requests[cmd.SetID] = accessRequest{SetID: cmd.SetID, Guest: set.Guest, By: who, At: time.Now()}
		s.reqMu.Unlock()
		s.Notice(fmt.Sprintf("%s asks to control the screen from their phone", who))
		s.BroadcastState()
		return nil
	case "cancel_request":
		s.dropRequest(cmd.SetID)
		s.BroadcastState()
		return nil
	case "take":
		if !inSet(cmd.AssetID) {
			return errors.New("that file isn't in your set")
		}
		if cmd.EntryID != 0 {
			if e := set.Entry(cmd.EntryID); e == nil || e.AssetID != cmd.AssetID {
				return errors.New("that clip isn't in your set")
			}
		}
		if _, ok := s.Engine.Resolve(cmd.AssetID); !ok {
			return errors.New("that file is still being copied to the vMix PC; try again in a minute")
		}
		s.Notice(fmt.Sprintf("%s (phone) put %s on screen", who, name(cmd.AssetID)))
		clip := s.clipFor(cmd.AssetID, cmd.EntryID)
		go func() { _ = s.Engine.TakeClip(cmd.AssetID, cmd.StartMs, clip) }()
	case "clear":
		if !liveIsTheirs {
			return errors.New("nothing of yours is on screen")
		}
		s.Notice(fmt.Sprintf("%s (phone) went back to the default", who))
		if live.AssetID == 0 && live.Audio.AssetID != 0 {
			go func() { _ = s.Engine.StopAudio() }()
		} else {
			go func() { _ = s.Engine.Clear() }()
		}
	case "stop_audio":
		if live.Audio.AssetID == 0 || !inSet(live.Audio.AssetID) {
			return errors.New("none of your audio is playing")
		}
		go func() { _ = s.Engine.StopAudio() }()
	case "pause", "play", "seek":
		if !liveIsTheirs {
			return errors.New("nothing of yours is playing")
		}
		go func() {
			switch cmd.Action {
			case "pause":
				_ = s.Engine.Pause()
			case "play":
				_ = s.Engine.Play()
			default:
				_ = s.Engine.Seek(cmd.PositionMs)
			}
		}()
	default:
		return fmt.Errorf("unknown action %q", cmd.Action)
	}
	return nil
}

// LiveForServer is the state the server shows on guests' pages (and uses to check control).
func (s *Server) LiveForServer() map[string]any {
	st := s.Engine.Status()
	controls := []int64{}
	for _, c := range s.controlsView() {
		controls = append(controls, c.SetID)
	}
	requests := []int64{}
	for _, r := range s.requestsView() {
		requests = append(requests, r.SetID)
	}
	var active int64
	s.Store.Read(func(st *store.State) { active = store.ActiveService(st) })
	return map[string]any{
		"type":       "live",
		"engine":     st,
		"controls":   controls,
		"requests":   requests,
		"service_id": active,
	}
}
