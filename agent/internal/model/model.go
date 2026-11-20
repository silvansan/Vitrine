// Package model holds the data the server sends in its manifest.
package model

// Manifest is what GET /api/agent/manifest returns.
type Manifest struct {
	Version        int       `json:"version"`
	ServerTime     string    `json:"server_time"`
	Agent          AgentInfo `json:"agent"`
	PubKey         string    `json:"pubkey"`
	Services       []Service `json:"services"`
	Sets           []Set     `json:"sets"`
	Assets         []Asset   `json:"assets"`
	RevokedScreens []string  `json:"revoked_screens"`
}

type AgentInfo struct {
	ID   int64  `json:"id"`
	Name string `json:"name"`
}

type Service struct {
	ID   int64  `json:"id"`
	Name string `json:"name"`
	Date string `json:"date"`
}

// Set is one guest's ordered list of files for a service.
type Set struct {
	ID        int64   `json:"id"`
	ServiceID int64   `json:"service_id"`
	Title     string  `json:"title"`
	Guest     string  `json:"guest"`
	Items     []int64 `json:"items"`
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
