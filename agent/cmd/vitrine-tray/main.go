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
	"path/filepath"
	"strings"
	"time"

	"fyne.io/systray"
	"golang.org/x/sys/windows"
)

//go:embed icon.ico
var iconNormal []byte

//go:embed icon_live.ico
var iconLive []byte

//go:embed icon_off.ico
var iconOff []byte

var base = "http://127.0.0.1:8090"

// alreadyRunning keeps one tray icon per logged-in user.
func alreadyRunning() bool {
	name, _ := windows.UTF16PtrFromString(`Local\VitrineTray`)
	_, err := windows.CreateMutex(nil, false, name)
	return err == windows.ERROR_ALREADY_EXISTS
}

func main() {
	if alreadyRunning() {
		return
	}
	if port := os.Getenv("VITRINE_PORT"); port != "" {
		base = "http://127.0.0.1:" + port
	}
	systray.Run(onReady, func() {})
}

func open(target string) {
	_ = exec.Command("rundll32", "url.dll,FileProtocolHandler", target).Start()
}

// agentExe is vitrine-agent.exe next to this program.
func agentExe() string {
	self, err := os.Executable()
	if err != nil {
		return "vitrine-agent.exe"
	}
	return filepath.Join(filepath.Dir(self), "vitrine-agent.exe")
}

// runAsAdmin starts vitrine-agent.exe with a command; Windows asks for permission.
func runAsAdmin(args string) {
	verb, _ := windows.UTF16PtrFromString("runas")
	file, _ := windows.UTF16PtrFromString(agentExe())
	params, _ := windows.UTF16PtrFromString(args)
	dir, _ := windows.UTF16PtrFromString(filepath.Dir(agentExe()))
	_ = windows.ShellExecute(0, verb, file, params, dir, windows.SW_HIDE)
}

func restartAgent() bool {
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Post(base+"/api/companion/restart", "application/json", strings.NewReader("{}"))
	if err != nil {
		return false
	}
	resp.Body.Close()
	return resp.StatusCode == http.StatusOK
}

func logPath() string {
	dir := os.Getenv("ProgramData")
	if dir == "" {
		dir = `C:\ProgramData`
	}
	return filepath.Join(dir, "Vitrine", "agent.log")
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
		Configured   bool `json:"configured"`
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
	logItem := systray.AddMenuItem("Open log", "")
	systray.AddSeparator()
	restart := systray.AddMenuItem("Restart agent", "Restarts the agent (tablets reconnect by themselves)")
	start := systray.AddMenuItem("Start agent", "Starts the Windows service (asks for permission)")
	stop := systray.AddMenuItem("Stop agent", "Stops the Windows service (asks for permission)")
	start.Hide()
	systray.AddSeparator()
	quit := systray.AddMenuItem("Close tray icon", "Closes only this icon; the agent keeps running as a service")

	mediaDir := ""
	go func() {
		for {
			st, err := fetch()
			if err != nil {
				start.Show()
				stop.Hide()
				restart.Disable()
				settings.Disable()
				pair.Disable()
				systray.SetIcon(iconOff)
				status.SetTitle("Agent not running")
				detail.SetTitle("Use Start agent below")
				systray.SetTooltip("Vitrine: agent not running")
			} else {
				start.Hide()
				stop.Show()
				restart.Enable()
				settings.Enable()
				pair.Enable()
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
				if !st.Status.Configured {
					label = "Not connected: open settings"
				} else if !st.Status.ServerOnline {
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
			case <-logItem.ClickedCh:
				_ = exec.Command("notepad", logPath()).Start()
			case <-restart.ClickedCh:
				if !restartAgent() {
					runAsAdmin("start")
				}
			case <-start.ClickedCh:
				runAsAdmin("start")
			case <-stop.ClickedCh:
				runAsAdmin("stop")
			case <-quit.ClickedCh:
				systray.Quit()
				return
			}
		}
	}()
}
