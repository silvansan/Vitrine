//go:build !windows

package main

import "fmt"

func main() {
	fmt.Println("vitrine-tray is the Windows tray icon. On other systems open http://127.0.0.1:8090/companion.")
}
