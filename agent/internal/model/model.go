// Package model holds the data the server sends in its manifest.
package model

// Manifest is what GET /api/agent/manifest returns.
type Manifest struct {
	Version    int       `json:"version"`
	ServerTime string    `json:"server_time"`
	Agent      AgentInfo `json:"agent"`
	// The venue this agent serves (server 0.2+; nil from older servers).
	Location       *LocationInfo `json:"location"`
	PubKey         string        `json:"pubkey"`
	Services       []Service     `json:"services"`
	Sets           []Set         `json:"sets"`
	Assets         []Asset       `json:"assets"`
	RevokedScreens []string      `json:"revoked_screens"`
}

type LocationInfo struct {
	ID           int64  `json:"id"`
	Name         string `json:"name"`
	Organization string `json:"organization"`
}

type AgentInfo struct {
	ID   int64  `json:"id"`
	Name string `json:"name"`
}

type Service struct {
	ID   int64  `json:"id"`
	Name string `json:"name"`
	// Start date ("" = already running). Events can last several days.
	Date   string `json:"date"`
	EndsOn string `json:"ends_on"`
	// Ready-made labels from the server: "27–29 Nov 2026" / "27–29 Nov", "ongoing".
	When      string `json:"when"`
	ShortWhen string `json:"short_when"`
}

// Set is one guest's ordered list of files for a service.
type Set struct {
	ID        int64   `json:"id"`
	ServiceID int64   `json:"service_id"`
	Title     string  `json:"title"`
	Guest     string  `json:"guest"`
	Items     []int64 `json:"items"` // each file once
	// Every entry in order, with clip marks (server 0.1.20+). A video can be in it several
	// times, once per clip. Empty from older servers: use List.
	Entries []Entry `json:"entries"`
}

// Entry is one tile of a set: a file, or a clip of a video (mark in → mark out).
type Entry struct {
	ID      int64  `json:"id"` // the server's set item; 0 from older servers
	AssetID int64  `json:"asset_id"`
	Clip    bool   `json:"clip"`
	InMs    int    `json:"in_ms"`
	OutMs   int    `json:"out_ms"` // 0: to the end
	Label   string `json:"label"`
	AtEnd   string `json:"at_end"` // at the mark out: "hold" (last frame) or "fade" (to the default input)
}

// List returns the set's entries in order (built from Items for older servers).
func (s Set) List() []Entry {
	if len(s.Entries) > 0 {
		return s.Entries
	}
	out := make([]Entry, 0, len(s.Items))
	for _, id := range s.Items {
		out = append(out, Entry{AssetID: id})
	}
	return out
}

// Entry returns the set entry with the given id, or nil.
func (s Set) Entry(id int64) *Entry {
	for i := range s.Entries {
		if s.Entries[i].ID == id {
			return &s.Entries[i]
		}
	}
	return nil
}

type Marker struct {
	ID    int64  `json:"id"`
	Label string `json:"label"`
	TMs   int    `json:"t_ms"`
}

// Asset is one processed photo or video in the library.
type Asset struct {
	ID                  int64    `json:"id"`
	Kind                string   `json:"kind"`
	Owner               string   `json:"owner"`
	OwnerSlug           string   `json:"owner_slug"`
	Name                string   `json:"name"`
	LocalName           string   `json:"local_name"`
	SHA256              string   `json:"sha256"`
	Size                int64    `json:"size"`
	DurationMs          int      `json:"duration_ms"`
	Width               int      `json:"width"`
	Height              int      `json:"height"`
	File                string   `json:"file"`
	Thumb               string   `json:"thumb"`
	Filmstrip           string   `json:"filmstrip"`
	FilmstripCount      int      `json:"filmstrip_count"`
	FilmstripCols       int      `json:"filmstrip_cols"`
	FilmstripIntervalMs int      `json:"filmstrip_interval_ms"`
	Markers             []Marker `json:"markers"`
}

// IsVideo reports whether the asset is a video.
func (a Asset) IsVideo() bool { return a.Kind == "video" }

// IsTimed reports whether the asset plays over time (video or audio).
func (a Asset) IsTimed() bool { return a.Kind == "video" || a.Kind == "audio" }

// FindEntry looks for a set entry by id in all sets.
func (m *Manifest) FindEntry(id int64) (*Set, *Entry) {
	if m == nil || id == 0 {
		return nil, nil
	}
	for i := range m.Sets {
		if e := m.Sets[i].Entry(id); e != nil {
			return &m.Sets[i], e
		}
	}
	return nil, nil
}

// Service returns the service with the given id, or nil.
func (m *Manifest) Service(id int64) *Service {
	if m == nil {
		return nil
	}
	for i := range m.Services {
		if m.Services[i].ID == id {
			return &m.Services[i]
		}
	}
	return nil
}

// Asset returns the asset with the given id, or nil.
func (m *Manifest) Asset(id int64) *Asset {
	if m == nil {
		return nil
	}
	for i := range m.Assets {
		if m.Assets[i].ID == id {
			return &m.Assets[i]
		}
	}
	return nil
}

// SetsFor returns the sets of one service, in manifest order.
func (m *Manifest) SetsFor(serviceID int64) []Set {
	out := []Set{}
	if m == nil {
		return out
	}
	for _, s := range m.Sets {
		if s.ServiceID == serviceID {
			out = append(out, s)
		}
	}
	return out
}
