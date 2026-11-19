// Package config loads the agent's config file.
//
// The config file holds what is fixed for one PC (server address, token,
// folders). Settings that change during a service (projector mix, default
// input, tablets) live in the state file and are edited in the companion.
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

type Config struct {
	// Address of the Vitrine server over Tailscale, e.g. http://100.101.102.103:8080
	ServerURL string `json:"server_url"`
	// Agent token from the backend (Agents & tablets > Create agent token).
	AgentToken string `json:"agent_token"`
	// Where downloaded media is kept. vMix plays the files from here.
	MediaDir string `json:"media_dir"`
	// Where the agent keeps its state, thumbnails and logs. Defaults to the config file's folder.
	DataDir string `json:"data_dir"`
	// vMix web API. Keep the default when the agent runs on the vMix PC.
	VMixURL string `json:"vmix_url"`
	// Address the tablets connect to.
	Listen string `json:"listen"`
	// LAN address shown in pairing QR codes. Empty = detect.
	AdvertiseIP string `json:"advertise_ip"`
	// The vMix API's Mix parameter is the mix number plus this offset (Mix 2 -> 1 by default).
	MixParamOffset int `json:"mix_param_offset"`
	// Download speed limit while something is live, in KB/s. 0 = no limit.
	LiveThrottleKBps int `json:"live_throttle_kbps"`

	Path string `json:"-"`
}

// DefaultPath is where the service looks for its config.
func DefaultPath() string {
	if runtime.GOOS == "windows" {
		base := os.Getenv("ProgramData")
		if base == "" {
			base = `C:\ProgramData`
		}
		return filepath.Join(base, "Vitrine", "config.json")
	}
	return filepath.Join("vitrine-data", "config.json")
}

func defaults(path string) Config {
	dir := filepath.Dir(path)
	return Config{
		MediaDir:         filepath.Join(dir, "media"),
		DataDir:          dir,
		VMixURL:          "http://127.0.0.1:8088",
		Listen:           ":8090",
		MixParamOffset:   -1,
		LiveThrottleKBps: 4096,
	}
}

// Load reads the config file, creating a template if it does not exist.
func Load(path string) (*Config, error) {
	cfg := defaults(path)
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			return nil, err
		}
		// First start: write the defaults. The server address and token are
		// filled in from the settings page (tray icon > Open settings).
		cfg.Path = path
		if err := cfg.Save(); err != nil {
			return nil, err
		}
		data, err = os.ReadFile(path)
	}
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("config %s: %w", path, err)
	}
	cfg.Path = path
	cfg.ServerURL = strings.TrimRight(cfg.ServerURL, "/")
	cfg.VMixURL = strings.TrimRight(cfg.VMixURL, "/")
	if cfg.DataDir == "" {
		cfg.DataDir = filepath.Dir(path)
	}
	if cfg.MediaDir == "" {
		cfg.MediaDir = filepath.Join(cfg.DataDir, "media")
	}
	for _, dir := range []string{cfg.DataDir, cfg.MediaDir} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, err
		}
	}
	return &cfg, nil
}

// ListenPort returns the port part of Listen.
func (c *Config) ListenPort() int {
	port := 8090
	if i := strings.LastIndex(c.Listen, ":"); i >= 0 {
		fmt.Sscanf(c.Listen[i+1:], "%d", &port)
	}
	return port
}

// Save writes the config file.
func (c *Config) Save() error {
	out, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	tmp := c.Path + ".tmp"
	if err := os.WriteFile(tmp, out, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, c.Path)
}

// Configured reports whether the server address and token are filled in.
func (c *Config) Configured() bool {
	return strings.HasPrefix(c.ServerURL, "http") && !strings.Contains(c.ServerURL, "x.y.z") &&
		strings.HasPrefix(c.AgentToken, "vta_") && !strings.Contains(c.AgentToken, "paste")
}

// Validate checks values entered in the settings page.
func (c *Config) Validate() error {
	if c.ServerURL != "" && !strings.HasPrefix(c.ServerURL, "http://") && !strings.HasPrefix(c.ServerURL, "https://") {
		return errors.New("the server address must start with http:// or https://")
	}
	if c.AgentToken != "" && !strings.HasPrefix(c.AgentToken, "vta_") {
		return errors.New("the agent token starts with vta_; copy it again from the backend")
	}
	if !strings.HasPrefix(c.VMixURL, "http://") && !strings.HasPrefix(c.VMixURL, "https://") {
		return errors.New("the vMix address must start with http://")
	}
	if strings.TrimSpace(c.MediaDir) == "" {
		return errors.New("choose a media folder")
	}
	if err := os.MkdirAll(c.MediaDir, 0o755); err != nil {
		return fmt.Errorf("can't use the media folder: %w", err)
	}
	port := c.ListenPort()
	if port < 1 || port > 65535 {
		return errors.New("the tablet port must be between 1 and 65535")
	}
	if c.LiveThrottleKBps < 0 {
		c.LiveThrottleKBps = 0
	}
	return nil
}
