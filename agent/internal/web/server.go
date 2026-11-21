// Package web serves the tablet app, the companion page and their APIs on the LAN.
package web

import (
	"context"
	"crypto/rand"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"io/fs"
	"log"
	"math/big"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"

	"github.com/silvansan/vitrine/agent/internal/config"
	"github.com/silvansan/vitrine/agent/internal/engine"
	"github.com/silvansan/vitrine/agent/internal/model"
	"github.com/silvansan/vitrine/agent/internal/pairing"
	"github.com/silvansan/vitrine/agent/internal/serverapi"
	"github.com/silvansan/vitrine/agent/internal/store"
	"github.com/silvansan/vitrine/agent/internal/syncer"
)

//go:embed static
var staticFS embed.FS

const cookieName = "vitrine_screen"

type Server struct {
	Cfg          *config.Config
	Store        *store.Store
	Engine       *engine.Engine
	Syncer       *syncer.Syncer
	API          *serverapi.Client
	ServerOnline func() bool
	LANIP        func() string
	TriggerSync  func()
	ServerError  func() string
	Restart      func()
	AddMarker    func(assetID int64, tMs int, label, by string) error
	DeleteMarker func(id int64) error
	Version      string

	hub      *Hub
	seenMu   sync.Mutex
	lastSeen map[string]time.Time

	// Hand-over codes: move a tablet's pairing into its home-screen app (iOS keeps
	// home-screen apps' cookies apart from Safari's).
	handoffMu    sync.Mutex
	handoffs     map[string]handoff
	handoffFails []time.Time
}

type handoff struct {
	screenID string
	expires  time.Time
}

func (s *Server) Hub() *Hub { return s.hub }

// Handler builds the HTTP routes.
func (s *Server) Handler() http.Handler {
	s.hub = newHub()
	s.lastSeen = map[string]time.Time{}
	static, _ := fs.Sub(staticFS, "static")
	mux := http.NewServeMux()

	mux.Handle("GET /static/", http.StripPrefix("/static/", http.FileServer(http.FS(static))))
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		if isLoopback(r) {
			http.Redirect(w, r, "/companion", http.StatusFound)
			return
		}
		http.Redirect(w, r, "/screen", http.StatusFound)
	})
	mux.HandleFunc("GET /pair", s.handlePair)
	mux.HandleFunc("GET /screen", s.handleScreen)
	mux.HandleFunc("GET /companion", s.handleCompanion)

	mux.HandleFunc("GET /api/bootstrap", s.screenOnly(s.handleBootstrap))
	mux.HandleFunc("GET /ws", s.handleWS)
	mux.HandleFunc("GET /m/{kind}/{id}", s.screenOrAdmin(s.handleMedia))

	mux.HandleFunc("POST /api/take", s.screenOnly(s.handleTake))
	mux.HandleFunc("POST /api/clear", s.screenOnly(s.simple(s.Engine.Clear)))
	mux.HandleFunc("POST /api/pause", s.screenOnly(s.simple(s.Engine.Pause)))
	mux.HandleFunc("POST /api/play", s.screenOnly(s.simple(s.Engine.Play)))
	mux.HandleFunc("POST /api/audio/stop", s.screenOnly(s.simple(s.Engine.StopAudio)))
	mux.HandleFunc("POST /api/seek", s.screenOnly(s.handleSeek))
	mux.HandleFunc("POST /api/handoff/new", s.screenOnly(s.handleHandoffNew))
	mux.HandleFunc("POST /api/handoff", s.handleHandoffUse)
	mux.HandleFunc("POST /api/screens/{id}", s.screenOnly(s.handleScreenUpdate))
	mux.HandleFunc("POST /api/screens/{id}/command", s.screenOnly(s.handleScreenCommand))
	mux.HandleFunc("POST /api/pulpit", s.screenOnly(s.handlePulpitSet))
	mux.HandleFunc("POST /api/markers", s.screenOnly(s.handleMarkerAdd))
	mux.HandleFunc("POST /api/markers/delete", s.screenOnly(s.handleMarkerDelete))

	mux.HandleFunc("GET /api/companion/state", s.adminOnly(s.handleCompanionState))
	mux.HandleFunc("POST /api/companion/settings", s.adminOnly(s.handleSettings))
	mux.HandleFunc("POST /api/companion/pair", s.adminOnly(s.handleCompanionPair))
	mux.HandleFunc("POST /api/companion/screens/{id}/delete", s.adminOnly(s.handleScreenDelete))
	mux.HandleFunc("POST /api/companion/test-default", s.adminOnly(s.simple(s.Engine.ShowDefault)))
	mux.HandleFunc("GET /api/companion/config", s.localOnly(s.handleConfigGet))
	mux.HandleFunc("POST /api/companion/config", s.localOnly(s.handleConfigSave))
	mux.HandleFunc("POST /api/companion/restart", s.localOnly(func(w http.ResponseWriter, r *http.Request, _ *store.Screen) {
		writeJSON(w, map[string]bool{"ok": true})
		s.Restart()
	}))
	mux.HandleFunc("GET /api/companion/log", s.localOnly(s.handleLog))
	mux.HandleFunc("GET /api/companion/keys", s.localOnly(s.handleKeysList))
	mux.HandleFunc("POST /api/companion/keys", s.localOnly(s.handleKeyCreate))
	mux.HandleFunc("POST /api/companion/keys/{id}/delete", s.localOnly(s.handleKeyDelete))
	mux.HandleFunc("POST /api/companion/sync", s.adminOnly(func(w http.ResponseWriter, r *http.Request, _ *store.Screen) {
		s.TriggerSync()
		writeJSON(w, map[string]bool{"ok": true})
	}))
	return logRequests(mux)
}

func logRequests(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/api/") && r.Method == http.MethodPost {
			log.Printf("%s %s from %s", r.Method, r.URL.Path, r.RemoteAddr)
		}
		next.ServeHTTP(w, r)
	})
}

// ---------- auth ----------

func isLoopback(r *http.Request) bool {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return false
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// integrationKey returns the key sent as "Authorization: Bearer vik_…" or ?key=.
func integrationKey(r *http.Request) string {
	if h := r.Header.Get("Authorization"); strings.HasPrefix(h, "Bearer vik_") {
		return strings.TrimPrefix(h, "Bearer ")
	}
	if k := r.URL.Query().Get("key"); strings.HasPrefix(k, "vik_") {
		return k
	}
	return ""
}

func (s *Server) screenFor(r *http.Request) *store.Screen {
	if key := integrationKey(r); key != "" {
		// An integration acts like a back tablet.
		hash := pairing.HashToken(key)
		var found *store.Screen
		s.Store.Read(func(st *store.State) {
			for _, k := range st.IntegrationKeys {
				if k.Hash == hash {
					found = &store.Screen{ID: "int-" + k.ID, Name: k.Name, Role: "back", Options: store.DefaultBackOptions(), Theme: "dark"}
					return
				}
			}
		})
		if found != nil {
			s.touchKey(strings.TrimPrefix(found.ID, "int-"))
		}
		return found
	}
	cookie, err := r.Cookie(cookieName)
	if err != nil || cookie.Value == "" {
		return nil
	}
	hash := pairing.HashToken(cookie.Value)
	var found *store.Screen
	s.Store.Read(func(st *store.State) {
		for _, sc := range st.Screens {
			if sc.TokenHash == hash {
				copy := *sc
				found = &copy
				return
			}
		}
	})
	if found != nil {
		s.touch(found.ID)
	}
	return found
}

func (s *Server) touchKey(id string) {
	s.seenMu.Lock()
	last := s.lastSeen["int-"+id]
	if time.Since(last) < time.Minute {
		s.seenMu.Unlock()
		return
	}
	s.lastSeen["int-"+id] = time.Now()
	s.seenMu.Unlock()
	_ = s.Store.Update(func(st *store.State) {
		for _, k := range st.IntegrationKeys {
			if k.ID == id {
				k.LastUsed = time.Now()
			}
		}
	})
}

func (s *Server) touch(id string) {
	s.seenMu.Lock()
	last := s.lastSeen[id]
	if time.Since(last) < time.Minute {
		s.seenMu.Unlock()
		return
	}
	s.lastSeen[id] = time.Now()
	s.seenMu.Unlock()
	_ = s.Store.Update(func(st *store.State) {
		if sc := st.Screens[id]; sc != nil {
			sc.LastSeen = time.Now()
		}
	})
}

type handler func(w http.ResponseWriter, r *http.Request, sc *store.Screen)

func (s *Server) screenOnly(h handler) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sc := s.screenFor(r)
		if sc == nil {
			writeError(w, http.StatusUnauthorized, "This tablet is not paired. Scan a pairing QR code.")
			return
		}
		h(w, r, sc)
	}
}

func (s *Server) adminOnly(h handler) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sc := s.screenFor(r)
		if !isLoopback(r) && (sc == nil || sc.Role != "back") {
			writeError(w, http.StatusForbidden, "Only on the vMix PC or the back tablet.")
			return
		}
		h(w, r, sc)
	}
}

// localOnly is for things only the vMix PC itself may see (the agent token, the log).
func (s *Server) localOnly(h handler) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !isLoopback(r) {
			writeError(w, http.StatusForbidden, "Only on the vMix PC itself.")
			return
		}
		h(w, r, nil)
	}
}

func (s *Server) screenOrAdmin(h handler) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sc := s.screenFor(r)
		if sc == nil && !isLoopback(r) {
			writeError(w, http.StatusUnauthorized, "not paired")
			return
		}
		h(w, r, sc)
	}
}

func (s *Server) simple(fn func() error) handler {
	return func(w http.ResponseWriter, r *http.Request, _ *store.Screen) {
		if err := fn(); err != nil {
			writeError(w, http.StatusConflict, err.Error())
			return
		}
		writeJSON(w, map[string]bool{"ok": true})
	}
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_ = json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, status int, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": msg})
}

func readJSON(r *http.Request, v any) error {
	defer r.Body.Close()
	return json.NewDecoder(http.MaxBytesReader(nil, r.Body, 1<<20)).Decode(v)
}

// ---------- pages ----------

func (s *Server) serveStatic(w http.ResponseWriter, name string) {
	data, err := staticFS.ReadFile("static/" + name)
	if err != nil {
		http.NotFound(w, nil)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	_, _ = w.Write(data)
}

func (s *Server) handleScreen(w http.ResponseWriter, r *http.Request) {
	if s.screenFor(r) == nil {
		s.serveStatic(w, "notpaired.html")
		return
	}
	s.serveStatic(w, "screen.html")
}

func (s *Server) handleCompanion(w http.ResponseWriter, r *http.Request) {
	sc := s.screenFor(r)
	if !isLoopback(r) && (sc == nil || sc.Role != "back") {
		http.Error(w, "The companion opens on the vMix PC (http://127.0.0.1"+s.Cfg.Listen+"/companion) or from the back tablet.", http.StatusForbidden)
		return
	}
	s.serveStatic(w, "companion.html")
}

func pairMessage(w http.ResponseWriter, status int, title, text string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	fmt.Fprintf(w, `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title><link rel="stylesheet" href="/static/app.css"></head><body class="theme-dark"><main class="message-page"><h1>%s</h1><p>%s</p></main></body></html>`,
		html.EscapeString(title), html.EscapeString(title), html.EscapeString(text))
}

func randomID() string {
	buf := make([]byte, 6)
	_, _ = rand.Read(buf)
	return hex.EncodeToString(buf)
}

func (s *Server) handlePair(w http.ResponseWriter, r *http.Request) {
	token := r.URL.Query().Get("t")
	manifest := s.Store.Manifest()
	var pub string
	var agentID int64
	if manifest != nil {
		pub = manifest.PubKey
		agentID = manifest.Agent.ID
	}
	var secret string
	s.Store.Read(func(st *store.State) { secret = st.AgentSecret })
	claims, err := pairing.Verify(token, pub, agentID, secret, time.Now())
	if err != nil {
		pairMessage(w, http.StatusBadRequest, "Pairing failed", err.Error())
		return
	}
	revoked := false
	if manifest != nil {
		for _, n := range manifest.RevokedScreens {
			if n == claims.Nonce {
				revoked = true
			}
		}
	}
	sessionToken, hash := pairing.NewSessionToken()
	var used bool
	var screen *store.Screen
	oldHash := ""
	if c, err := r.Cookie(cookieName); err == nil {
		oldHash = pairing.HashToken(c.Value)
	}
	_ = s.Store.Update(func(st *store.State) {
		if _, ok := st.UsedNonces[claims.Nonce]; ok || revoked {
			used = true
			return
		}
		st.UsedNonces[claims.Nonce] = time.Now()
		for id, sc := range st.Screens {
			if oldHash != "" && sc.TokenHash == oldHash {
				delete(st.Screens, id)
			}
		}
		options := store.DefaultPulpitOptions()
		theme := "same"
		if claims.Role == "back" {
			options = store.DefaultBackOptions()
			theme = "dark"
		}
		screen = &store.Screen{ID: randomID(), Name: claims.Name, Role: claims.Role, TokenHash: hash, Nonce: claims.Nonce,
			Theme: theme, Options: options, Created: time.Now(), LastSeen: time.Now()}
		st.Screens[screen.ID] = screen
	})
	if used {
		pairMessage(w, http.StatusBadRequest, "Pairing failed", "This pairing code was already used or has been revoked. Make a new one.")
		return
	}
	http.SetCookie(w, &http.Cookie{Name: cookieName, Value: sessionToken, Path: "/", HttpOnly: true,
		SameSite: http.SameSiteLaxMode, MaxAge: 5 * 365 * 24 * 3600})
	log.Printf("paired %s (%s) as screen %s", claims.Name, claims.Role, screen.ID)
	s.broadcastScreens()
	http.Redirect(w, r, "/screen", http.StatusFound)
}

// ---------- catalog ----------

type tabletAsset struct {
	ID                  int64          `json:"id"`
	Kind                string         `json:"kind"`
	Owner               string         `json:"owner"`
	Name                string         `json:"name"`
	DurationMs          int            `json:"duration_ms"`
	Thumb               string         `json:"thumb"`
	Filmstrip           string         `json:"filmstrip"`
	FilmstripCount      int            `json:"filmstrip_count"`
	FilmstripCols       int            `json:"filmstrip_cols"`
	FilmstripIntervalMs int            `json:"filmstrip_interval_ms"`
	Markers             []model.Marker `json:"markers"`
	Ready               bool           `json:"ready"`
}

type publicScreen struct {
	ID           string              `json:"id"`
	Name         string              `json:"name"`
	Role         string              `json:"role"`
	CurrentSetID int64               `json:"current_set_id"`
	Theme        string              `json:"theme"`
	Options      store.ScreenOptions `json:"options"`
	LastSeen     time.Time           `json:"last_seen"`
	Online       bool                `json:"online"`
}

func (s *Server) screensLocked(st *store.State) []publicScreen {
	online := map[string]bool{}
	s.hub.mu.Lock()
	for c := range s.hub.clients {
		online[c.screenID] = true
	}
	s.hub.mu.Unlock()
	out := []publicScreen{}
	for _, sc := range st.Screens {
		out = append(out, publicScreen{ID: sc.ID, Name: sc.Name, Role: sc.Role, CurrentSetID: sc.CurrentSetID,
			Theme: sc.Theme, Options: sc.Options, LastSeen: sc.LastSeen, Online: online[sc.ID]})
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Role != out[j].Role {
			return out[i].Role < out[j].Role
		}
		return out[i].Name < out[j].Name
	})
	return out
}

// Screens returns the paired tablets (for the server heartbeat).
func (s *Server) Screens() []map[string]any {
	out := []map[string]any{}
	s.Store.Read(func(st *store.State) {
		titles := map[int64]string{}
		if st.Manifest != nil {
			for _, set := range st.Manifest.Sets {
				titles[set.ID] = set.Title
			}
		}
		for _, sc := range s.screensLocked(st) {
			out = append(out, map[string]any{"id": sc.ID, "name": sc.Name, "role": sc.Role,
				"current_set_id": sc.CurrentSetID, "current_set_title": titles[sc.CurrentSetID],
				"last_seen": sc.LastSeen.Format("02 Jan 15:04"), "online": sc.Online})
		}
	})
	return out
}

func (s *Server) catalog(sc *store.Screen) map[string]any {
	out := map[string]any{}
	s.Store.Read(func(st *store.State) {
		m := st.Manifest
		serviceID := store.ActiveService(st)
		sets := m.SetsFor(serviceID)
		assets := map[string]tabletAsset{}
		if m != nil {
			for _, set := range sets {
				for _, id := range set.Items {
					a := m.Asset(id)
					if a == nil {
						continue
					}
					f := st.Files[id]
					ta := tabletAsset{ID: a.ID, Kind: a.Kind, Owner: a.Owner, Name: a.Name, DurationMs: a.DurationMs,
						FilmstripCount: a.FilmstripCount, FilmstripCols: a.FilmstripCols, FilmstripIntervalMs: a.FilmstripIntervalMs,
						Markers: a.Markers}
					if ta.Markers == nil {
						ta.Markers = []model.Marker{}
					}
					if f != nil && f.SHA256 == a.SHA256 {
						ta.Ready = true
						ta.Thumb = fmt.Sprintf("/m/thumb/%d?v=%s", a.ID, shortSHA(a.SHA256))
						if f.Filmstrip != "" {
							ta.Filmstrip = fmt.Sprintf("/m/filmstrip/%d?v=%s", a.ID, shortSHA(a.SHA256))
						}
					}
					assets[strconv.FormatInt(a.ID, 10)] = ta
				}
			}
		}
		var service *model.Service
		var services []model.Service
		if m != nil {
			service = m.Service(serviceID)
			services = m.Services
		}
		out["sets"] = sets
		out["assets"] = assets
		out["service"] = service
		out["services"] = services
		// 0 = follow today's (or the next) event automatically.
		out["service_pinned"] = st.Settings.ServiceID != 0 && service != nil && service.ID == st.Settings.ServiceID
		if m != nil {
			out["location"] = m.Location
		}
		out["screens"] = s.screensLocked(st)
		out["settings"] = st.Settings
		out["positions"] = positionsJSON(st.Positions)
		if sc != nil {
			if live := st.Screens[sc.ID]; live != nil {
				out["screen"] = publicScreen{ID: live.ID, Name: live.Name, Role: live.Role, CurrentSetID: live.CurrentSetID,
					Theme: live.Theme, Options: live.Options, LastSeen: live.LastSeen}
			}
		}
	})
	out["status"] = s.statusMessage()
	return out
}

func shortSHA(sha string) string {
	if len(sha) > 8 {
		return sha[:8]
	}
	return sha
}

func positionsJSON(p map[int64]int) map[string]int {
	out := map[string]int{}
	for k, v := range p {
		out[strconv.FormatInt(k, 10)] = v
	}
	return out
}

func (s *Server) statusMessage() map[string]any {
	var positions map[string]int
	s.Store.Read(func(st *store.State) { positions = positionsJSON(st.Positions) })
	online := false
	if s.ServerOnline != nil {
		online = s.ServerOnline()
	}
	serverError := ""
	if s.ServerError != nil && !online {
		serverError = s.ServerError()
	}
	return map[string]any{
		"type":          "state",
		"engine":        s.Engine.Status(),
		"positions":     positions,
		"sync":          s.Syncer.Status(),
		"server_online": online,
		"server_error":  serverError,
		"configured":    s.Cfg.Configured(),
	}
}

// BroadcastState pushes the live state to every tablet.
func (s *Server) BroadcastState() {
	if s.hub == nil {
		return
	}
	s.hub.Broadcast(s.statusMessage())
}

// BroadcastCatalog tells tablets to reload sets and files.
func (s *Server) BroadcastCatalog() {
	if s.hub == nil {
		return
	}
	s.hub.Broadcast(map[string]string{"type": "catalog"})
}

func (s *Server) broadcastScreens() {
	var screens []publicScreen
	s.Store.Read(func(st *store.State) { screens = s.screensLocked(st) })
	s.hub.Broadcast(map[string]any{"type": "screens", "screens": screens})
}

// ---------- tablet API ----------

func (s *Server) handleBootstrap(w http.ResponseWriter, r *http.Request, sc *store.Screen) {
	writeJSON(w, s.catalog(sc))
}

func (s *Server) handleWS(w http.ResponseWriter, r *http.Request) {
	sc := s.screenFor(r)
	if sc == nil && !isLoopback(r) {
		http.Error(w, "not paired", http.StatusUnauthorized)
		return
	}
	conn, err := websocket.Accept(w, r, nil)
	if err != nil {
		return
	}
	c := &client{conn: conn, send: make(chan []byte, 32)}
	if sc != nil {
		c.screenID = sc.ID
	}
	first, _ := json.Marshal(s.statusMessage())
	go s.broadcastScreensSoon()
	s.hub.serve(r.Context(), c, first)
	go s.broadcastScreensSoon()
}

func (s *Server) broadcastScreensSoon() {
	time.Sleep(300 * time.Millisecond)
	s.broadcastScreens()
}

func (s *Server) handleMedia(w http.ResponseWriter, r *http.Request, _ *store.Screen) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	var path string
	s.Store.Read(func(st *store.State) {
		if f := st.Files[id]; f != nil {
			switch r.PathValue("kind") {
			case "thumb":
				path = f.Thumb
			case "filmstrip":
				path = f.Filmstrip
			}
		}
	})
	if path == "" {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Cache-Control", "private, max-age=604800")
	http.ServeFile(w, r, path)
}

func (s *Server) handleTake(w http.ResponseWriter, r *http.Request, _ *store.Screen) {
	var body struct {
		AssetID int64 `json:"asset_id"`
		StartMs int   `json:"start_ms"`
		// Alternative to asset_id, for integrations: guest number and file number (1-based).
		Guest int `json:"guest"`
		File  int `json:"file"`
		// "resume" starts where it was stopped last time.
		From string `json:"from"`
	}
	if err := readJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "bad request")
		return
	}
	if body.AssetID == 0 && body.Guest > 0 && body.File > 0 {
		id, ok := s.fileAt(body.Guest, body.File)
		if !ok {
			writeError(w, http.StatusNotFound, fmt.Sprintf("guest %d has no file %d", body.Guest, body.File))
			return
		}
		body.AssetID = id
	}
	if body.From == "resume" {
		s.Store.Read(func(st *store.State) { body.StartMs = st.Positions[body.AssetID] })
	}
	// Answer right away; the tablets follow progress (Sending… / live) over the WebSocket.
	go func() { _ = s.Engine.Take(body.AssetID, body.StartMs) }()
	writeJSON(w, map[string]bool{"ok": true})
}

func (s *Server) handleSeek(w http.ResponseWriter, r *http.Request, sc *store.Screen) {
	var body struct {
		PositionMs int `json:"position_ms"`
	}
	if err := readJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "bad request")
		return
	}
	go func() { _ = s.Engine.Seek(body.PositionMs) }()
	writeJSON(w, map[string]bool{"ok": true})
}

func (s *Server) handleScreenUpdate(w http.ResponseWriter, r *http.Request, sc *store.Screen) {
	id := r.PathValue("id")
	var body struct {
		Name         *string              `json:"name"`
		CurrentSetID *int64               `json:"current_set_id"`
		Theme        *string              `json:"theme"`
		Options      *store.ScreenOptions `json:"options"`
	}
	if err := readJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "bad request")
		return
	}
	if id == "self" {
		id = sc.ID
	}
	isBack := sc.Role == "back" || isLoopback(r)
	if id != sc.ID && !isBack {
		writeError(w, http.StatusForbidden, "only the back tablet can change other tablets")
		return
	}
	var notFound bool
	_ = s.Store.Update(func(st *store.State) {
		target := st.Screens[id]
		if target == nil {
			notFound = true
			return
		}
		if body.Theme != nil && (*body.Theme == "dark" || *body.Theme == "light" || *body.Theme == "same") {
			if isBack {
				target.Theme = *body.Theme
			}
		}
		if !isBack {
			return
		}
		if body.Name != nil && strings.TrimSpace(*body.Name) != "" {
			target.Name = strings.TrimSpace(*body.Name)
		}
		if body.CurrentSetID != nil {
			target.CurrentSetID = *body.CurrentSetID
		}
		if body.Options != nil {
			target.Options = *body.Options
		}
	})
	if notFound {
		writeError(w, http.StatusNotFound, "no such tablet")
		return
	}
	s.broadcastScreens()
	writeJSON(w, map[string]bool{"ok": true})
}

func (s *Server) handlePulpitSet(w http.ResponseWriter, r *http.Request, sc *store.Screen) {
	if sc.Role != "back" {
		writeError(w, http.StatusForbidden, "only the back tablet can change the pulpit")
		return
	}
	var body struct {
		CurrentSetID int64 `json:"current_set_id"`
		Guest        int   `json:"guest"`
	}
	if err := readJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "bad request")
		return
	}
	if body.CurrentSetID == 0 && body.Guest > 0 {
		set, ok := s.setAt(body.Guest)
		if !ok {
			writeError(w, http.StatusNotFound, fmt.Sprintf("there is no guest %d", body.Guest))
			return
		}
		body.CurrentSetID = set.ID
	}
	_ = s.Store.Update(func(st *store.State) {
		for _, screen := range st.Screens {
			if screen.Role == "pulpit" {
				screen.CurrentSetID = body.CurrentSetID
			}
		}
	})
	s.broadcastScreens()
	writeJSON(w, map[string]bool{"ok": true})
}

func (s *Server) handleScreenCommand(w http.ResponseWriter, r *http.Request, sc *store.Screen) {
	if sc.Role != "back" {
		writeError(w, http.StatusForbidden, "only the back tablet can send commands")
		return
	}
	var body map[string]any
	if err := readJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "bad request")
		return
	}
	body["type"] = "command"
	s.hub.SendTo(r.PathValue("id"), body)
	writeJSON(w, map[string]bool{"ok": true})
}

// ---------- companion API ----------

func (s *Server) handleCompanionState(w http.ResponseWriter, r *http.Request, _ *store.Screen) {
	out := s.catalog(nil)
	type inputInfo struct {
		Key    string `json:"key"`
		Number int    `json:"number"`
		Name   string `json:"name"`
		Type   string `json:"type"`
	}
	inputs := []inputInfo{}
	mixes := []int{1, 2, 3, 4}
	if st := s.Engine.LastState(); st != nil {
		for _, in := range st.Inputs {
			inputs = append(inputs, inputInfo{Key: in.Key, Number: in.Number, Name: in.Name(), Type: in.Type})
		}
		if found := st.Mixes(); len(found) > 1 {
			mixes = found
		}
	}
	out["inputs"] = inputs
	out["mixes"] = mixes
	out["transitions"] = engine.Transitions()
	out["agent"] = map[string]any{
		"version":    s.Version,
		"lan_ip":     s.LANIP(),
		"port":       s.Cfg.ListenPort(),
		"server_url": s.Cfg.ServerURL,
		"media_dir":  s.Cfg.MediaDir,
		"vmix_url":   s.Cfg.VMixURL,
	}
	writeJSON(w, out)
}

func (s *Server) handleSettings(w http.ResponseWriter, r *http.Request, _ *store.Screen) {
	var body map[string]json.RawMessage
	if err := readJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "bad request")
		return
	}
	var serviceChanged bool
	err := s.Store.Update(func(st *store.State) {
		cur := st.Settings
		raw, _ := json.Marshal(cur)
		merged := map[string]json.RawMessage{}
		_ = json.Unmarshal(raw, &merged)
		for k, v := range body {
			merged[k] = v
		}
		data, _ := json.Marshal(merged)
		var next store.Settings
		if err := json.Unmarshal(data, &next); err != nil {
			return
		}
		if next.Mix < 1 {
			next.Mix = 1
		}
		if next.TransitionMs < 0 || next.TransitionMs > 5000 {
			next.TransitionMs = 500
		}
		if next.SeekMode != "direct" {
			next.SeekMode = "crossfade"
		}
		if strings.TrimSpace(next.InputA) == "" {
			next.InputA = "GM_A"
		}
		if strings.TrimSpace(next.InputB) == "" {
			next.InputB = "GM_B"
		}
		if strings.TrimSpace(next.InputAudio) == "" {
			next.InputAudio = "GM_AUDIO"
		}
		serviceChanged = next.ServiceID != cur.ServiceID
		st.Settings = next
	})
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if serviceChanged {
		s.BroadcastCatalog()
		s.TriggerSync()
	} else {
		s.hub.Broadcast(map[string]any{"type": "settings", "settings": s.Store.Settings()})
	}
	s.BroadcastState()
	writeJSON(w, map[string]bool{"ok": true})
}

func (s *Server) handleCompanionPair(w http.ResponseWriter, r *http.Request, _ *store.Screen) {
	var body struct {
		Role string `json:"role"`
		Name string `json:"name"`
	}
	if err := readJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "bad request")
		return
	}
	if body.Role != "back" {
		body.Role = "pulpit"
	}
	if strings.TrimSpace(body.Name) == "" {
		if body.Role == "back" {
			body.Name = "Back tablet"
		} else {
			body.Name = "Pulpit tablet"
		}
	}
	token := ""
	source := "server"
	if s.ServerOnline() {
		ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
		t, err := s.API.Pairing(ctx, body.Role, body.Name)
		cancel()
		if err == nil {
			token = t
		} else {
			log.Printf("server pairing failed, issuing a local code: %v", err)
		}
	}
	if token == "" {
		var secret string
		s.Store.Read(func(st *store.State) { secret = st.AgentSecret })
		token = pairing.IssueLocal(secret, body.Role, body.Name, 15*time.Minute)
		source = "agent"
	}
	url := fmt.Sprintf("http://%s:%d/pair?t=%s", s.LANIP(), s.Cfg.ListenPort(), token)
	writeJSON(w, map[string]string{"url": url, "source": source})
}

func (s *Server) handleScreenDelete(w http.ResponseWriter, r *http.Request, _ *store.Screen) {
	id := r.PathValue("id")
	_ = s.Store.Update(func(st *store.State) { delete(st.Screens, id) })
	s.hub.Disconnect(id)
	s.broadcastScreens()
	writeJSON(w, map[string]bool{"ok": true})
}

// ApplyRevocations unpairs tablets whose pairing code was revoked in the backend.
func (s *Server) ApplyRevocations(m *model.Manifest) {
	if m == nil || len(m.RevokedScreens) == 0 {
		return
	}
	revoked := map[string]bool{}
	for _, n := range m.RevokedScreens {
		revoked[n] = true
	}
	var gone []string
	_ = s.Store.Update(func(st *store.State) {
		for id, sc := range st.Screens {
			if revoked[sc.Nonce] {
				gone = append(gone, id)
				delete(st.Screens, id)
			}
		}
	})
	for _, id := range gone {
		log.Printf("unpaired screen %s (revoked in backend)", id)
		s.hub.Disconnect(id)
	}
	if len(gone) > 0 {
		s.broadcastScreens()
	}
}

// ListenAndServe runs the HTTP server until ctx ends.
func (s *Server) ListenAndServe(ctx context.Context) error {
	srv := &http.Server{Addr: s.Cfg.Listen, Handler: s.Handler(), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		s.hub.CloseAll()
		shutdown, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdown)
	}()
	log.Printf("tablets: http://%s:%d/screen  companion: http://127.0.0.1:%d/companion", s.LANIP(), s.Cfg.ListenPort(), s.Cfg.ListenPort())
	err := srv.ListenAndServe()
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

// ---------- connection settings (config.json) ----------

type configView struct {
	ServerURL        string `json:"server_url"`
	TokenSet         bool   `json:"token_set"`
	TokenPrefix      string `json:"token_prefix"`
	MediaDir         string `json:"media_dir"`
	VMixURL          string `json:"vmix_url"`
	Listen           string `json:"listen"`
	AdvertiseIP      string `json:"advertise_ip"`
	MixParamOffset   int    `json:"mix_param_offset"`
	LiveThrottleKBps int    `json:"live_throttle_kbps"`
	Path             string `json:"path"`
	LogPath          string `json:"log_path"`
}

func (s *Server) handleConfigGet(w http.ResponseWriter, r *http.Request, _ *store.Screen) {
	c := s.Cfg
	prefix := ""
	if len(c.AgentToken) > 12 && strings.HasPrefix(c.AgentToken, "vta_") {
		prefix = c.AgentToken[:12]
	}
	writeJSON(w, configView{ServerURL: c.ServerURL, TokenSet: c.Configured(), TokenPrefix: prefix, MediaDir: c.MediaDir,
		VMixURL: c.VMixURL, Listen: c.Listen, AdvertiseIP: c.AdvertiseIP, MixParamOffset: c.MixParamOffset,
		LiveThrottleKBps: c.LiveThrottleKBps, Path: c.Path, LogPath: filepath.Join(c.DataDir, "agent.log")})
}

func (s *Server) handleConfigSave(w http.ResponseWriter, r *http.Request, _ *store.Screen) {
	var body struct {
		ServerURL        string `json:"server_url"`
		AgentToken       string `json:"agent_token"`
		MediaDir         string `json:"media_dir"`
		VMixURL          string `json:"vmix_url"`
		Listen           string `json:"listen"`
		AdvertiseIP      string `json:"advertise_ip"`
		MixParamOffset   int    `json:"mix_param_offset"`
		LiveThrottleKBps int    `json:"live_throttle_kbps"`
	}
	if err := readJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "bad request")
		return
	}
	next := *s.Cfg
	next.ServerURL = strings.TrimRight(strings.TrimSpace(body.ServerURL), "/")
	if token := strings.TrimSpace(body.AgentToken); token != "" {
		next.AgentToken = token
	}
	next.MediaDir = strings.TrimSpace(body.MediaDir)
	next.VMixURL = strings.TrimRight(strings.TrimSpace(body.VMixURL), "/")
	next.Listen = strings.TrimSpace(body.Listen)
	if next.Listen != "" && !strings.Contains(next.Listen, ":") {
		next.Listen = ":" + next.Listen
	}
	next.AdvertiseIP = strings.TrimSpace(body.AdvertiseIP)
	next.MixParamOffset = body.MixParamOffset
	next.LiveThrottleKBps = body.LiveThrottleKBps
	if err := next.Validate(); err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if next.ServerURL != "" && next.AgentToken != "" {
		ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
		_, err := serverapi.New(next.ServerURL, next.AgentToken).Manifest(ctx)
		cancel()
		if err != nil && r.URL.Query().Get("force") != "1" {
			writeError(w, http.StatusBadGateway, "Couldn't reach the server with these settings: "+err.Error())
			return
		}
	}
	if err := next.Save(); err != nil {
		writeError(w, http.StatusInternalServerError, "Couldn't save "+next.Path+": "+err.Error())
		return
	}
	log.Printf("connection settings saved to %s", next.Path)
	writeJSON(w, map[string]any{"ok": true, "restart": true, "port": next.ListenPort()})
	s.Restart()
}

func (s *Server) handleLog(w http.ResponseWriter, r *http.Request, _ *store.Screen) {
	path := filepath.Join(s.Cfg.DataDir, "agent.log")
	data, err := os.ReadFile(path)
	if err != nil {
		writeJSON(w, map[string]string{"log": "", "path": path})
		return
	}
	if len(data) > 40000 {
		data = data[len(data)-40000:]
	}
	writeJSON(w, map[string]string{"log": string(data), "path": path})
}

// ---------- markers ("Add marker here") ----------

func (s *Server) handleMarkerAdd(w http.ResponseWriter, r *http.Request, sc *store.Screen) {
	var body struct {
		AssetID int64  `json:"asset_id"`
		TMs     int    `json:"t_ms"`
		Label   string `json:"label"`
	}
	if err := readJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "bad request")
		return
	}
	label := strings.TrimSpace(body.Label)
	if label == "" {
		total := body.TMs / 1000
		label = fmt.Sprintf("Marker %d:%02d", total/60, total%60)
	}
	if len(label) > 60 {
		label = label[:60]
	}
	if err := s.AddMarker(body.AssetID, body.TMs, label, sc.Name); err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	writeJSON(w, map[string]bool{"ok": true})
}

func (s *Server) handleMarkerDelete(w http.ResponseWriter, r *http.Request, sc *store.Screen) {
	if sc.Role != "back" {
		writeError(w, http.StatusForbidden, "only the back tablet can remove markers")
		return
	}
	var body struct {
		ID int64 `json:"id"`
	}
	if err := readJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "bad request")
		return
	}
	if err := s.DeleteMarker(body.ID); err != nil {
		writeError(w, http.StatusConflict, err.Error())
		return
	}
	writeJSON(w, map[string]bool{"ok": true})
}

// ---------- guests and files by number (for integrations) ----------

// setAt returns the n-th guest set (1-based) of the active service, in tablet order.
func (s *Server) setAt(n int) (model.Set, bool) {
	var out model.Set
	ok := false
	s.Store.Read(func(st *store.State) {
		sets := st.Manifest.SetsFor(store.ActiveService(st))
		if n >= 1 && n <= len(sets) {
			out = sets[n-1]
			ok = true
		}
	})
	return out, ok
}

// fileAt returns the asset id of file f (1-based) of guest g, counting only files on this PC.
func (s *Server) fileAt(g, f int) (int64, bool) {
	set, ok := s.setAt(g)
	if !ok {
		return 0, false
	}
	n := 0
	var id int64
	s.Store.Read(func(st *store.State) {
		for _, assetID := range set.Items {
			if st.Manifest.Asset(assetID) == nil {
				continue
			}
			n++
			if n == f {
				id = assetID
				return
			}
		}
	})
	return id, id != 0
}

// ---------- integration keys ----------

type keyView struct {
	ID       string    `json:"id"`
	Name     string    `json:"name"`
	Prefix   string    `json:"prefix"`
	Created  time.Time `json:"created"`
	LastUsed time.Time `json:"last_used"`
}

func (s *Server) handleKeysList(w http.ResponseWriter, r *http.Request, _ *store.Screen) {
	out := []keyView{}
	s.Store.Read(func(st *store.State) {
		for _, k := range st.IntegrationKeys {
			out = append(out, keyView{ID: k.ID, Name: k.Name, Prefix: k.Prefix, Created: k.Created, LastUsed: k.LastUsed})
		}
	})
	writeJSON(w, out)
}

func (s *Server) handleKeyCreate(w http.ResponseWriter, r *http.Request, _ *store.Screen) {
	var body struct {
		Name string `json:"name"`
	}
	_ = readJSON(r, &body)
	name := strings.TrimSpace(body.Name)
	if name == "" {
		name = "Bitfocus Companion"
	}
	raw, _ := pairing.NewSessionToken()
	key := "vik_" + raw
	k := &store.IntegrationKey{ID: randomID(), Name: name, Hash: pairing.HashToken(key), Prefix: key[:10], Created: time.Now()}
	_ = s.Store.Update(func(st *store.State) { st.IntegrationKeys = append(st.IntegrationKeys, k) })
	log.Printf("integration key %q created", name)
	writeJSON(w, map[string]string{"id": k.ID, "key": key, "name": name})
}

func (s *Server) handleKeyDelete(w http.ResponseWriter, r *http.Request, _ *store.Screen) {
	id := r.PathValue("id")
	_ = s.Store.Update(func(st *store.State) {
		kept := st.IntegrationKeys[:0]
		for _, k := range st.IntegrationKeys {
			if k.ID != id {
				kept = append(kept, k)
			}
		}
		st.IntegrationKeys = kept
	})
	s.hub.Disconnect("int-" + id)
	writeJSON(w, map[string]bool{"ok": true})
}

// ---------- hand-over to the home-screen app ----------

// handleHandoffNew gives this tablet a 6-digit code (10 minutes) to sign in its home-screen app.
func (s *Server) handleHandoffNew(w http.ResponseWriter, r *http.Request, sc *store.Screen) {
	if strings.HasPrefix(sc.ID, "int-") {
		writeError(w, http.StatusBadRequest, "integrations can't hand over")
		return
	}
	s.handoffMu.Lock()
	defer s.handoffMu.Unlock()
	if s.handoffs == nil {
		s.handoffs = map[string]handoff{}
	}
	for code, h := range s.handoffs {
		if time.Now().After(h.expires) || h.screenID == sc.ID {
			delete(s.handoffs, code)
		}
	}
	var code string
	for {
		n, _ := rand.Int(rand.Reader, big.NewInt(1000000))
		code = fmt.Sprintf("%06d", n.Int64())
		if _, taken := s.handoffs[code]; !taken {
			break
		}
	}
	s.handoffs[code] = handoff{screenID: sc.ID, expires: time.Now().Add(10 * time.Minute)}
	writeJSON(w, map[string]any{"code": code, "minutes": 10})
}

// handleHandoffUse signs this browser in as the tablet that made the code. The tablet's
// session moves here (the browser tab it came from is signed out).
func (s *Server) handleHandoffUse(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Code string `json:"code"`
	}
	if err := readJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "bad request")
		return
	}
	code := strings.TrimSpace(body.Code)
	s.handoffMu.Lock()
	recent := s.handoffFails[:0]
	for _, t := range s.handoffFails {
		if time.Since(t) < time.Minute {
			recent = append(recent, t)
		}
	}
	s.handoffFails = recent
	if len(recent) >= 10 {
		s.handoffMu.Unlock()
		writeError(w, http.StatusTooManyRequests, "Too many wrong codes. Wait a minute.")
		return
	}
	h, ok := s.handoffs[code]
	if !ok || time.Now().After(h.expires) {
		s.handoffFails = append(s.handoffFails, time.Now())
		s.handoffMu.Unlock()
		writeError(w, http.StatusNotFound, "That code isn't valid (any more). Make a new one on the tablet.")
		return
	}
	delete(s.handoffs, code)
	s.handoffMu.Unlock()

	sessionToken, hash := pairing.NewSessionToken()
	found := false
	_ = s.Store.Update(func(st *store.State) {
		if sc, ok := st.Screens[h.screenID]; ok {
			sc.TokenHash = hash
			sc.LastSeen = time.Now()
			found = true
		}
	})
	if !found {
		writeError(w, http.StatusNotFound, "That tablet was unpaired.")
		return
	}
	s.hub.Disconnect(h.screenID)
	http.SetCookie(w, &http.Cookie{Name: cookieName, Value: sessionToken, Path: "/", HttpOnly: true,
		SameSite: http.SameSiteLaxMode, MaxAge: 5 * 365 * 24 * 3600})
	log.Printf("screen %s moved to its home-screen app", h.screenID)
	writeJSON(w, map[string]bool{"ok": true})
}
