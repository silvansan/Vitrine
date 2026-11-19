//go:build windows

// vitrine-tray is the tray icon on the vMix PC. It shows the agent's status
// and opens the companion page (settings, default input, pairing tablets).
package main

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"time"

	"fyne.io/systray"
)

//go:embed icon.ico
var iconNormal []byte

//go:embed icon_live.ico
var iconLive []byte

//go:embed icon_off.ico
var iconOff []byte

var base = "http://127.0.0.1:8090"

func main() {
	if port := os.Getenv("VITRINE_PORT"); port != "" {
		base = "http://127.0.0.1:" + port
	}
	systray.Run(onReady, func() {})
}

func open(target string) {
	_ = exec.Command("rundll32", "url.dll,FileProtocolHandler", target).Start()
}

type companionState struct {
	Status struct {
		Engine struct {
			AssetID      int64  `json:"asset_id"`
			VMixOK       bool   `json:"vmix_ok"`
			DefaultOK    bool   `json:"default_ok"`
			DefaultTitle string `json:"default_title"`
			Mix          int    `json:"mix"`
		} `json:"engine"`
		Sync struct {
			Total int `json:"total"`
			Done  int `json:"done"`
		} `json:"sync"`
		ServerOnline bool `json:"server_online"`
	} `json:"status"`
	Service *struct {
		Name string `json:"name"`
	} `json:"service"`
	Agent struct {
		MediaDir string `json:"media_dir"`
	} `json:"agent"`
}

func fetch() (*companionState, error) {
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get(base + "/api/companion/state")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var st companionState
	if err := json.NewDecoder(resp.Body).Decode(&st); err != nil {
		return nil, err
	}
	return &st, nil
}

func onReady() {
	systray.SetIcon(iconOff)
	systray.SetTitle("Vitrine")
	systray.SetTooltip("Vitrine agent")

	status := systray.AddMenuItem("Starting…", "")
	status.Disable()
	detail := systray.AddMenuItem("", "")
	detail.Disable()
	systray.AddSeparator()
	settings := systray.AddMenuItem("Open settings…", "Projector mix, default input, tablets")
	pair := systray.AddMenuItem("Pair a tablet…", "Show a pairing QR code")
	media := systray.AddMenuItem("Open media folder", "")
	systray.AddSeparator()
	quit := systray.AddMenuItem("Hide tray icon", "The agent keeps running as a service")

	mediaDir := ""
	go func() {
		for {
			st, err := fetch()
			if err != nil {
				systray.SetIcon(iconOff)
				status.SetTitle("Agent not running")
				detail.SetTitle("Start the VitrineAgent service")
				systray.SetTooltip("Vitrine: agent not running")
			} else {
				mediaDir = st.Agent.MediaDir
				eng := st.Status.Engine
				label := "Ready"
				icon := iconNormal
				switch {
				case !eng.VMixOK:
					label = "vMix not reachable"
					icon = iconOff
				case eng.AssetID != 0:
					label = fmt.Sprintf("Live on Mix %d", eng.Mix)
					icon = iconLive
				case !eng.DefaultOK:
					label = "Choose a default input"
				}
				if !st.Status.ServerOnline {
					label += " · server offline"
				}
				systray.SetIcon(icon)
				status.SetTitle(label)
				service := "no session"
				if st.Service != nil {
					service = st.Service.Name
				}
				detail.SetTitle(fmt.Sprintf("%s · %d/%d files", service, st.Status.Sync.Done, st.Status.Sync.Total))
				systray.SetTooltip("Vitrine: " + label)
			}
			time.Sleep(3 * time.Second)
		}
	}()

	go func() {
		for {
			select {
			case <-settings.ClickedCh:
				open(base + "/companion")
			case <-pair.ClickedCh:
				open(base + "/companion#pair")
			case <-media.ClickedCh:
				if mediaDir != "" {
					_ = exec.Command("explorer", mediaDir).Start()
				}
			case <-quit.ClickedCh:
				systray.Quit()
				return
			}
		}
	}()
}
