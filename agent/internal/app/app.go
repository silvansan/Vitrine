// Package app wires the agent together: server sync, vMix engine and the LAN web server.
package app

import (
	"context"
	"errors"
	"log"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/silvansan/vitrine/agent/internal/config"
	"github.com/silvansan/vitrine/agent/internal/engine"
	"github.com/silvansan/vitrine/agent/internal/model"
	"github.com/silvansan/vitrine/agent/internal/netutil"
	"github.com/silvansan/vitrine/agent/internal/serverapi"
	"github.com/silvansan/vitrine/agent/internal/store"
	"github.com/silvansan/vitrine/agent/internal/syncer"
	"github.com/silvansan/vitrine/agent/internal/vmix"
	"github.com/silvansan/vitrine/agent/internal/web"
)

// Version is set at build time with -ldflags "-X .../app.Version=1.2.3".
var Version = "dev"

type App struct {
	Cfg    *config.Config
	Store  *store.Store
	Engine *engine.Engine
	Syncer *syncer.Syncer
	API    *serverapi.Client
	Web    *web.Server

	online   atomic.Bool
	srvErr   atomic.Value
	restart  atomic.Bool
	cancel   context.CancelFunc
	refresh  chan struct{}
	ctx      context.Context
	lanIP    atomic.Value
	lastVers int
	flushMu  sync.Mutex
}

func New(cfg *config.Config) (*App, error) {
	st, err := store.Open(cfg.DataDir)
	if err != nil {
		return nil, err
	}
	a := &App{Cfg: cfg, Store: st, refresh: make(chan struct{}, 1)}
	a.API = serverapi.New(cfg.ServerURL, cfg.AgentToken)
	a.lanIP.Store(a.detectIP())
	a.srvErr.Store("")

	a.Engine = &engine.Engine{VMix: vmix.New(cfg.VMixURL), Store: st, Cfg: cfg, Resolve: a.resolve}
	a.Syncer = &syncer.Syncer{
		Client:   a.API,
		Store:    st,
		MediaDir: cfg.MediaDir,
		DataDir:  cfg.DataDir,
		Throttle: func() int {
			if a.Engine.IsLive() {
				return cfg.LiveThrottleKBps
			}
			return 0
		},
		InUse: a.Engine.InUse,
	}
	a.Web = &web.Server{
		Cfg: cfg, Store: st, Engine: a.Engine, Syncer: a.Syncer, API: a.API, Version: Version,
		ServerOnline: a.online.Load,
		LANIP:        func() string { return a.lanIP.Load().(string) },
		TriggerSync:  func() { a.Syncer.Trigger(a.ctx) },
		ServerError:  func() string { return a.srvErr.Load().(string) },
		Restart:      a.Restart,
		AddMarker:    a.addMarker,
		DeleteMarker: a.deleteMarker,
	}
	var lastCatalog atomic.Int64
	a.Syncer.OnChanged = func() {
		// Coalesce catalog reloads while many files arrive.
		now := time.Now().UnixMilli()
		if now-lastCatalog.Load() > 1500 {
			lastCatalog.Store(now)
			a.Web.BroadcastCatalog()
		}
	}
	a.Engine.OnChange = a.Web.BroadcastState
	return a, nil
}

func (a *App) detectIP() string {
	if a.Cfg.AdvertiseIP != "" {
		return a.Cfg.AdvertiseIP
	}
	return netutil.LANIP()
}

func (a *App) resolve(id int64) (*engine.Media, bool) {
	var media *engine.Media
	a.Store.Read(func(st *store.State) {
		asset := st.Manifest.Asset(id)
		file := st.Files[id]
		if asset == nil || file == nil || file.SHA256 != asset.SHA256 {
			return
		}
		if _, err := os.Stat(file.Path); err != nil {
			return
		}
		path, _ := filepath.Abs(file.Path)
		media = &engine.Media{ID: id, Path: path, Kind: asset.Kind, DurationMs: asset.DurationMs, Name: asset.Name}
	})
	return media, media != nil
}

// Restart stops the agent and makes Run report that it should be started again
// (used after the settings page saves a new config).
func (a *App) Restart() {
	a.restart.Store(true)
	if a.cancel != nil {
		go func() {
			time.Sleep(300 * time.Millisecond) // let the HTTP answer go out first
			a.cancel()
		}()
	}
}

// RestartRequested reports whether Run ended because of Restart.
func (a *App) RestartRequested() bool { return a.restart.Load() }

func (a *App) setServerError(err error) {
	if err == nil {
		a.srvErr.Store("")
		return
	}
	a.srvErr.Store(err.Error())
}

// Run starts everything and blocks until ctx ends or Restart is called.
func (a *App) Run(parent context.Context) error {
	ctx, cancel := context.WithCancel(parent)
	defer cancel()
	a.cancel = cancel
	a.ctx = ctx
	log.Printf("Vitrine agent %s, server %s, media %s", Version, a.Cfg.ServerURL, a.Cfg.MediaDir)
	go a.Engine.Run(ctx)
	if a.Cfg.Configured() {
		go a.API.Listen(ctx, a.requestRefresh, func(connected bool) {
			was := a.online.Swap(connected)
			if was != connected {
				a.Web.BroadcastState()
			}
		})
		go a.manifestLoop(ctx)
		go a.heartbeatLoop(ctx)
	} else {
		a.srvErr.Store("Not connected yet: enter the server address and agent token under Connection.")
		log.Printf("server address or token missing; open the settings page to enter them")
	}
	// Files already downloaded are usable right away, even offline.
	a.Syncer.Trigger(ctx)
	return a.Web.ListenAndServe(ctx)
}

func (a *App) requestRefresh() {
	select {
	case a.refresh <- struct{}{}:
	default:
	}
}

func (a *App) manifestLoop(ctx context.Context) {
	ticker := time.NewTicker(60 * time.Second)
	defer ticker.Stop()
	a.requestRefresh()
	for {
		select {
		case <-ctx.Done():
			return
		case <-a.refresh:
		case <-ticker.C:
		}
		a.fetchManifest(ctx)
	}
}

func (a *App) fetchManifest(ctx context.Context) {
	reqCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	m, err := a.API.Manifest(reqCtx)
	a.setServerError(err)
	if err != nil {
		log.Printf("manifest: %v", err)
		return
	}
	changed := true
	_ = a.Store.Update(func(st *store.State) {
		if st.Manifest != nil && st.Manifest.Version == m.Version && a.lastVers == m.Version {
			changed = false
		}
		st.Manifest = m
		// Markers added on tablets that haven't reached the server yet stay visible.
		for _, p := range st.PendingMarkers {
			if asset := m.Asset(p.AssetID); asset != nil {
				asset.Markers = append(asset.Markers, model.Marker{Label: p.Label, TMs: p.TMs})
				sort.Slice(asset.Markers, func(i, j int) bool { return asset.Markers[i].TMs < asset.Markers[j].TMs })
			}
		}
	})
	a.lastVers = m.Version
	a.Web.ApplyRevocations(m)
	if changed {
		a.Web.BroadcastCatalog()
	}
	a.Syncer.Trigger(ctx)
}

func (a *App) heartbeatLoop(ctx context.Context) {
	ticker := time.NewTicker(20 * time.Second)
	defer ticker.Stop()
	for {
		a.lanIP.Store(a.detectIP())
		status := a.Engine.Status()
		used := a.Engine.TakeUsed()
		if used == nil {
			used = []int64{}
		}
		payload := map[string]any{
			"version":     Version,
			"lan_ip":      a.lanIP.Load().(string),
			"listen_port": a.Cfg.ListenPort(),
			"vmix": map[string]any{
				"ok": status.VMixOK, "version": status.VMixVersion, "inputs_ok": status.InputsOK,
				"default_ok": status.DefaultOK, "mix_active": status.MixActive,
			},
			"live":        map[string]any{"asset_id": status.AssetID, "paused": status.Paused},
			"sync":        a.Syncer.Status(),
			"settings":    a.Store.Settings(),
			"screens":     a.Web.Screens(),
			"used_assets": used,
			"service_id":  a.Store.ActiveServiceID(),
			"time":        time.Now().Format(time.RFC3339),
			"media_dir":   a.Cfg.MediaDir,
		}
		a.flushMarkers(ctx)
		reqCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
		version, err := a.API.Heartbeat(reqCtx, payload)
		cancel()
		a.setServerError(err)
		if err != nil {
			log.Printf("heartbeat: %v", err)
		} else if version != a.lastVers {
			a.requestRefresh()
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

// addMarker records a marker added on a tablet. It shows on every tablet at
// once and is sent to the server now, or as soon as the server is reachable.
func (a *App) addMarker(assetID int64, tMs int, label, by string) error {
	var found bool
	err := a.Store.Update(func(st *store.State) {
		asset := st.Manifest.Asset(assetID)
		if asset == nil || !asset.IsVideo() {
			return
		}
		found = true
		if tMs < 0 {
			tMs = 0
		}
		if asset.DurationMs > 0 && tMs > asset.DurationMs {
			tMs = asset.DurationMs
		}
		asset.Markers = append(asset.Markers, model.Marker{Label: label, TMs: tMs})
		sort.Slice(asset.Markers, func(i, j int) bool { return asset.Markers[i].TMs < asset.Markers[j].TMs })
		st.PendingMarkers = append(st.PendingMarkers, store.PendingMarker{AssetID: assetID, TMs: tMs, Label: label, By: by})
	})
	if err != nil {
		return err
	}
	if !found {
		return errors.New("markers can only be added to videos")
	}
	a.Web.BroadcastCatalog()
	go a.flushMarkers(a.ctx)
	return nil
}

// deleteMarker removes a marker on the server (needs the server to be reachable).
func (a *App) deleteMarker(id int64) error {
	if id == 0 {
		return errors.New("this marker is still being saved; try again in a moment")
	}
	if !a.online.Load() {
		return errors.New("markers can only be removed while the server is connected")
	}
	ctx, cancel := context.WithTimeout(a.ctx, 10*time.Second)
	defer cancel()
	if err := a.API.Markers(ctx, nil, []int64{id}); err != nil {
		return err
	}
	_ = a.Store.Update(func(st *store.State) {
		if st.Manifest == nil {
			return
		}
		for i := range st.Manifest.Assets {
			kept := st.Manifest.Assets[i].Markers[:0]
			for _, m := range st.Manifest.Assets[i].Markers {
				if m.ID != id {
					kept = append(kept, m)
				}
			}
			st.Manifest.Assets[i].Markers = kept
		}
	})
	a.Web.BroadcastCatalog()
	a.requestRefresh()
	return nil
}

// flushMarkers sends markers added offline to the server.
func (a *App) flushMarkers(ctx context.Context) {
	if ctx == nil || !a.Cfg.Configured() {
		return
	}
	a.flushMu.Lock()
	defer a.flushMu.Unlock()
	var pending []store.PendingMarker
	a.Store.Read(func(st *store.State) { pending = append(pending, st.PendingMarkers...) })
	if len(pending) == 0 {
		return
	}
	add := make([]serverapi.NewMarker, 0, len(pending))
	for _, p := range pending {
		add = append(add, serverapi.NewMarker{AssetID: p.AssetID, TMs: p.TMs, Label: p.Label, By: p.By})
	}
	reqCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if err := a.API.Markers(reqCtx, add, nil); err != nil {
		log.Printf("markers: will retry: %v", err)
		return
	}
	_ = a.Store.Update(func(st *store.State) {
		if len(st.PendingMarkers) >= len(pending) {
			st.PendingMarkers = st.PendingMarkers[len(pending):]
		} else {
			st.PendingMarkers = nil
		}
	})
	a.requestRefresh()
}
