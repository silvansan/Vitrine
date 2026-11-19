//go:build !windows

package service

import (
	"context"
	"errors"
)

const Name = "VitrineAgent"

var errWindowsOnly = errors.New("installing as a service is only supported on Windows; use `vitrine-agent run`")

func IsService() bool                                      { return false }
func RunService(run func(ctx context.Context) error) error { return errWindowsOnly }
func Install(configPath string, port int) error            { return errWindowsOnly }
func Uninstall() error                                     { return errWindowsOnly }
func Control(start bool) error                             { return errWindowsOnly }
func IsAdmin() bool                                        { return false }
func Elevate(args string) error                            { return errWindowsOnly }
func Setup(configPath string, port int) error              { return errWindowsOnly }
func Pause()                                               {}
