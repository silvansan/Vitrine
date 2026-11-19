//go:build windows

// Package service installs and runs the agent as a Windows service.
package service

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"golang.org/x/sys/windows"

	"golang.org/x/sys/windows/registry"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"
)

const Name = "VitrineAgent"
const displayName = "Vitrine agent"
const firewallRule = "Vitrine agent (tablets)"
const trayRunKey = `SOFTWARE\Microsoft\Windows\CurrentVersion\Run`
const trayValue = "VitrineTray"

// IsService reports whether the process was started by the service manager.
func IsService() bool {
	ok, err := svc.IsWindowsService()
	return err == nil && ok
}

type handler struct {
	run func(ctx context.Context) error
}

func (h *handler) Execute(args []string, requests <-chan svc.ChangeRequest, status chan<- svc.Status) (bool, uint32) {
	status <- svc.Status{State: svc.StartPending}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- h.run(ctx) }()
	status <- svc.Status{State: svc.Running, Accepts: svc.AcceptStop | svc.AcceptShutdown}
	for {
		select {
		case err := <-done:
			cancel()
			if err != nil {
				return true, 1
			}
			return false, 0
		case req := <-requests:
			switch req.Cmd {
			case svc.Interrogate:
				status <- req.CurrentStatus
			case svc.Stop, svc.Shutdown:
				status <- svc.Status{State: svc.StopPending}
				cancel()
				select {
				case <-done:
				case <-time.After(5 * time.Second):
				}
				return false, 0
			}
		}
	}
}

// RunService hands control to the Windows service manager.
func RunService(run func(ctx context.Context) error) error {
	return svc.Run(Name, &handler{run: run})
}

// Install registers the service (automatic start, restart on failure), opens
// the tablet port in the firewall and starts the tray companion at logon.
func Install(configPath string, port int) error {
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	exe, _ = filepath.Abs(exe)
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("run this from an administrator command prompt: %w", err)
	}
	defer m.Disconnect()
	s, err := m.OpenService(Name)
	if err == nil {
		// Already installed: point it at this exe (update), then restart below.
		fmt.Println("Service already installed: updating it to this version.")
		_, _ = s.Control(svc.Stop)
		waitStopped(s)
		cfg, cerr := s.Config()
		if cerr != nil {
			s.Close()
			return cerr
		}
		cfg.BinaryPathName = fmt.Sprintf(`"%s" service -config "%s"`, exe, configPath)
		if err := s.UpdateConfig(cfg); err != nil {
			s.Close()
			return err
		}
	} else {
		s, err = m.CreateService(Name, exe, mgr.Config{
			DisplayName:      displayName,
			Description:      "Syncs guest media from the Vitrine server and puts it on vMix from the tablets.",
			StartType:        mgr.StartAutomatic,
			DelayedAutoStart: true,
		}, "service", "-config", configPath)
		if err != nil {
			return err
		}
	}
	defer s.Close()
	_ = s.SetRecoveryActions([]mgr.RecoveryAction{
		{Type: mgr.ServiceRestart, Delay: 5 * time.Second},
		{Type: mgr.ServiceRestart, Delay: 10 * time.Second},
		{Type: mgr.ServiceRestart, Delay: 30 * time.Second},
	}, 3600)

	_ = exec.Command("netsh", "advfirewall", "firewall", "delete", "rule", "name="+firewallRule).Run()
	if out, err := exec.Command("netsh", "advfirewall", "firewall", "add", "rule", "name="+firewallRule,
		"dir=in", "action=allow", "protocol=TCP", fmt.Sprintf("localport=%d", port), "profile=any").CombinedOutput(); err != nil {
		fmt.Printf("warning: firewall rule not added: %v %s\n", err, out)
	}

	tray := filepath.Join(filepath.Dir(exe), "vitrine-tray.exe")
	if _, err := os.Stat(tray); err == nil {
		key, _, err := registry.CreateKey(registry.LOCAL_MACHINE, trayRunKey, registry.SET_VALUE)
		if err == nil {
			_ = key.SetStringValue(trayValue, `"`+tray+`"`)
			key.Close()
		}
	}
	return s.Start()
}

// Uninstall stops and removes the service, firewall rule and tray autostart.
func Uninstall() error {
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("run this from an administrator command prompt: %w", err)
	}
	defer m.Disconnect()
	s, err := m.OpenService(Name)
	if err != nil {
		return fmt.Errorf("service %s is not installed", Name)
	}
	defer s.Close()
	_, _ = s.Control(svc.Stop)
	waitStopped(s)
	if err := s.Delete(); err != nil {
		return err
	}
	_ = exec.Command("netsh", "advfirewall", "firewall", "delete", "rule", "name="+firewallRule).Run()
	if key, err := registry.OpenKey(registry.LOCAL_MACHINE, trayRunKey, registry.SET_VALUE); err == nil {
		_ = key.DeleteValue(trayValue)
		key.Close()
	}
	return nil
}

// Control starts or stops the installed service.
func Control(start bool) error {
	m, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer m.Disconnect()
	s, err := m.OpenService(Name)
	if err != nil {
		return fmt.Errorf("service %s is not installed", Name)
	}
	defer s.Close()
	if start {
		return s.Start()
	}
	_, err = s.Control(svc.Stop)
	return err
}

func waitStopped(s *mgr.Service) {
	for i := 0; i < 40; i++ {
		status, err := s.Query()
		if err != nil || status.State == svc.Stopped {
			return
		}
		time.Sleep(250 * time.Millisecond)
	}
}

// InstallDir is where Setup copies the program: %ProgramFiles%\Vitrine.
func InstallDir() string {
	base := os.Getenv("ProgramFiles")
	if base == "" {
		base = `C:\Program Files`
	}
	return filepath.Join(base, "Vitrine")
}

// IsAdmin reports whether this process runs elevated.
func IsAdmin() bool {
	return windows.GetCurrentProcessToken().IsElevated()
}

// Elevate starts this exe again as administrator with the given arguments.
func Elevate(args string) error {
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	verb, _ := windows.UTF16PtrFromString("runas")
	file, _ := windows.UTF16PtrFromString(exe)
	params, _ := windows.UTF16PtrFromString(args)
	dir, _ := windows.UTF16PtrFromString(filepath.Dir(exe))
	return windows.ShellExecute(0, verb, file, params, dir, windows.SW_SHOWNORMAL)
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	tmp := dst + ".new"
	out, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	if err := out.Close(); err != nil {
		return err
	}
	_ = os.Remove(dst)
	return os.Rename(tmp, dst)
}

// Setup is what a double-click does: copy the program to Program Files
// (stopping the running version first), install or update the service, start
// it, start the tray icon and open the settings page.
func Setup(configPath string, port int) error {
	src, err := os.Executable()
	if err != nil {
		return err
	}
	srcDir := filepath.Dir(src)
	dir := InstallDir()
	target := filepath.Join(dir, "vitrine-agent.exe")

	fmt.Println("Installing Vitrine agent into", dir)
	if m, err := mgr.Connect(); err == nil {
		if s, err := m.OpenService(Name); err == nil {
			fmt.Println("Stopping the running agent…")
			_, _ = s.Control(svc.Stop)
			waitStopped(s)
			s.Close()
		}
		m.Disconnect()
	}
	_ = exec.Command("taskkill", "/IM", "vitrine-tray.exe", "/F").Run()
	time.Sleep(500 * time.Millisecond)

	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	if !strings.EqualFold(filepath.Clean(srcDir), filepath.Clean(dir)) {
		for _, name := range []string{"vitrine-agent.exe", "vitrine-tray.exe", "vitrine-fakevmix.exe", "README.md"} {
			from := filepath.Join(srcDir, name)
			if _, err := os.Stat(from); err != nil {
				continue
			}
			if err := copyFile(from, filepath.Join(dir, name)); err != nil {
				return fmt.Errorf("copying %s: %w", name, err)
			}
		}
	}

	// Install (or update) the service from the copied exe.
	cmd := exec.Command(target, "install", "-config", configPath)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("installing the service failed: %w", err)
	}

	// Start the tray as the normal (not elevated) user, via Explorer.
	tray := filepath.Join(dir, "vitrine-tray.exe")
	if _, err := os.Stat(tray); err == nil {
		_ = exec.Command("explorer.exe", tray).Start()
	}
	time.Sleep(2 * time.Second)
	_ = exec.Command("rundll32", "url.dll,FileProtocolHandler", fmt.Sprintf("http://127.0.0.1:%d/companion", port)).Start()
	fmt.Println()
	fmt.Println("Done. The settings page is opening in your browser; fill in Connection there.")
	fmt.Println("Next time, double-click the new vitrine-agent.exe to update.")
	return nil
}

// Pause waits for Enter, so a double-clicked console window stays readable.
func Pause() {
	fmt.Println()
	fmt.Print("Press Enter to close this window.")
	_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
}
