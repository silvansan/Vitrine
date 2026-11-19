// vitrine-fakevmix pretends to be vMix's web API on :8088, so the agent and
// tablets can be tried on any computer. It logs every function call.
package main

import (
	"flag"
	"log"
	"net/http"

	"github.com/silvansan/vitrine/agent/internal/vmix/vmixfake"
)

func main() {
	addr := flag.String("listen", "127.0.0.1:8088", "address to listen on")
	flag.Parse()
	fake := vmixfake.New()
	fake.Verbose = true
	log.Printf("fake vMix on http://%s/api (inputs: 1 Banner Sunday, 2 Camera 1)", *addr)
	log.Fatal(http.ListenAndServe(*addr, fake))
}
