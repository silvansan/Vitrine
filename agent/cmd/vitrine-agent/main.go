// vitrine-agent runs on the vMix PC.
//
//	vitrine-agent install     install as a Windows service (run as administrator)
//	vitrine-agent uninstall   remove the service
//	vitrine-agent start|stop  control the installed service
//	vitrine-agent run         run in this console (for testing)
package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/silvansan/vitrine/agent/internal/app"
	"github.com/silvansan/vitrine/agent/internal/config"
	"github.com/silvansan/vitrine/agent/internal/service"
)

func usage() {
	fmt.Fprintf(os.Stderr, `Vitrine agent %s

Usage: vitrine-agent <command> [-config path]

Commands:
  install     install and start the Windows service (as administrator)
  uninstall   stop and remove the Windows service
  start       start the installed service
  stop        stop the installed service
  run         run in this window (Ctrl+C to stop)
  version     print the version

Default config: %s
`, app.Version, config.DefaultPath())
}

func setupLog(dataDir string, console bool) {
	_ = os.MkdirAll(dataDir, 0o755)
	path := filepath.Join(dataDir, "agent.log")
	if info, err := os.Stat(path); err == nil && info.Size() > 10<<20 {
		_ = os.Rename(path, path+".1")
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	if console {
		log.SetOutput(io.MultiWriter(os.Stderr, file))
	} else {
		log.SetOutput(file)
	}
}

func runAgent(configPath string, console bool) func(ctx context.Context) error {
	return func(ctx context.Context) error {
		for {
			cfg, err := config.Load(configPath)
			if err != nil {
				setupLog(filepath.Dir(configPath), console)
				log.Print(err)
				return err
			}
			setupLog(cfg.DataDir, console)
			a, err := app.New(cfg)
			if err != nil {
				log.Print(err)
				return err
			}
			err = a.Run(ctx)
			if ctx.Err() != nil {
				return nil
			}
			if !a.RestartRequested() {
				return err
			}
			log.Printf("restarting with the new settings")
			time.Sleep(500 * time.Millisecond)
		}
	}
}

func main() {
	if service.IsService() {
		path := config.DefaultPath()
		for i, arg := range os.Args {
			if arg == "-config" && i+1 < len(os.Args) {
				path = os.Args[i+1]
			}
		}
		if err := service.RunService(runAgent(path, false)); err != nil {
			log.Fatal(err)
		}
		return
	}

	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	command := os.Args[1]
	flags := flag.NewFlagSet(command, flag.ExitOnError)
	configPath := flags.String("config", config.DefaultPath(), "path to config.json")
	_ = flags.Parse(os.Args[2:])
	abs, _ := filepath.Abs(*configPath)

	var err error
	switch command {
	case "install":
		port := 8090
		if cfg, cerr := config.Load(abs); cerr == nil {
			port = cfg.ListenPort()
		} else {
			err = cerr
			break
		}
		err = service.Install(abs, port)
		if err == nil {
			fmt.Println("Installed and started.")
			fmt.Println("Next: start vitrine-tray.exe, then right-click its icon > Open settings, and fill in Connection.")
		}
	case "uninstall":
		err = service.Uninstall()
		if err == nil {
			fmt.Println("Removed.")
		}
	case "start":
		err = service.Control(true)
	case "stop":
		err = service.Control(false)
	case "run", "service":
		ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		err = runAgent(abs, true)(ctx)
	case "version":
		fmt.Println(app.Version)
	default:
		usage()
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}
