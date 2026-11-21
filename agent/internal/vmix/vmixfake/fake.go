// Package vmixfake is a tiny stand-in for the vMix web API, for tests and for
// trying the tablets without vMix (see cmd/vitrine-fakevmix).
package vmixfake

import (
	"encoding/xml"
	"fmt"
	"log"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

type input struct {
	Key, Title, Type string
	Number           int
	Items            []string
	Selected         int
	Playing          bool
	PosMs            int
	DurMs            int
	AudioOn          bool
	AudioAuto        bool
	Volume           float64
	Fades            []string
	lastTick         time.Time
}

type Fake struct {
	mu      sync.Mutex
	inputs  []*input
	active  map[int]int // mix number -> input number
	Calls   []string
	nextKey int
	Verbose bool
	// VideoDurationMs is the length the fake gives every .mp4.
	VideoDurationMs int
}

// New makes a fake with a banner (input 1) and a camera (input 2) on every mix.
func New() *Fake {
	f := &Fake{active: map[int]int{1: 2, 2: 1, 3: 1, 4: 1}, VideoDurationMs: 20000}
	f.add("Colour", "Banner Sunday")
	f.add("Capture", "Camera 1")
	return f
}

func (f *Fake) add(typ, title string) *input {
	f.nextKey++
	in := &input{Key: fmt.Sprintf("key-%04d", f.nextKey), Title: title, Type: typ, Number: len(f.inputs) + 1, Volume: 100}
	f.inputs = append(f.inputs, in)
	return in
}

func (f *Fake) find(ref string) *input {
	for _, in := range f.inputs {
		if in.Key == ref || strings.EqualFold(in.Title, ref) || strconv.Itoa(in.Number) == ref {
			return in
		}
	}
	return nil
}

func (f *Fake) tick() {
	now := time.Now()
	for _, in := range f.inputs {
		if in.Playing && !in.lastTick.IsZero() {
			in.PosMs += int(now.Sub(in.lastTick).Milliseconds())
			if in.DurMs > 0 && in.PosMs >= in.DurMs {
				in.PosMs = in.DurMs
				in.Playing = false
			}
		}
		in.lastTick = now
	}
}

// Active returns the title of the input on a mix.
func (f *Fake) Active(mix int) string {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, in := range f.inputs {
		if in.Number == f.active[mix] {
			return in.Title
		}
	}
	return ""
}

// Position returns an input's playback position.
func (f *Fake) Position(title string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.tick()
	if in := f.find(title); in != nil {
		return in.PosMs
	}
	return -1
}

// Audio reports whether an input's sound is on.
func (f *Fake) Audio(title string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	if in := f.find(title); in != nil {
		return in.AudioOn
	}
	return false
}

// Volume reports an input's volume (fades jump to their end value) and the fades sent to it.
func (f *Fake) Volume(title string) (float64, []string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if in := f.find(title); in != nil {
		return in.Volume, append([]string{}, in.Fades...)
	}
	return -1, nil
}

// Switch puts an input on a mix, as if the operator clicked in vMix.
func (f *Fake) Switch(mix int, title string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if in := f.find(title); in != nil {
		f.active[mix] = in.Number
	}
}

func (f *Fake) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.tick()
	q := r.URL.Query()
	fn := q.Get("Function")
	if fn == "" {
		f.writeXML(w)
		return
	}
	f.Calls = append(f.Calls, fn+" "+q.Get("Input")+" "+q.Get("Value")+" "+q.Get("Mix"))
	if f.Verbose {
		log.Printf("vMix %s Input=%s Value=%s Mix=%s Duration=%s", fn, q.Get("Input"), q.Get("Value"), q.Get("Mix"), q.Get("Duration"))
	}
	in := f.find(q.Get("Input"))
	switch fn {
	case "SetVolume":
		if in != nil {
			v, _ := strconv.ParseFloat(q.Get("Value"), 64)
			in.Volume = v
		}
	case "SetVolumeFade":
		if in != nil {
			parts := strings.SplitN(q.Get("Value"), ",", 2)
			v, _ := strconv.ParseFloat(parts[0], 64)
			in.Volume = v
			in.Fades = append(in.Fades, q.Get("Value"))
		}
	case "AudioOn", "AudioOff", "AudioAutoOn", "AudioAutoOff":
		if in != nil {
			switch fn {
			case "AudioOn":
				in.AudioOn = true
			case "AudioOff":
				in.AudioOn = false
			case "AudioAutoOn":
				in.AudioAuto = true
			default:
				in.AudioAuto = false
			}
		}
	case "AddInput":
		if strings.HasPrefix(q.Get("Value"), "VideoList") {
			f.add("VideoList", "List")
		}
	case "SetInputName":
		if in != nil {
			in.Title = q.Get("Value")
		}
	case "ListRemoveAll":
		if in != nil {
			in.Items = nil
			in.Selected = 0
			in.PosMs = 0
			in.DurMs = 0
			in.Playing = false
		}
	case "ListAdd":
		if in != nil {
			in.Items = append(in.Items, q.Get("Value"))
		}
	case "SelectIndex":
		if in != nil {
			n, _ := strconv.Atoi(q.Get("Value"))
			if n >= 1 && n <= len(in.Items) {
				in.Selected = n
				in.PosMs = 0
				in.DurMs = 0
				if strings.HasSuffix(strings.ToLower(in.Items[n-1]), ".mp4") {
					in.DurMs = f.VideoDurationMs
				}
			}
		}
	case "SetPosition":
		if in != nil {
			in.PosMs, _ = strconv.Atoi(q.Get("Value"))
		}
	case "Play":
		if in != nil {
			in.Playing = true
		}
	case "Pause":
		if in != nil {
			in.Playing = false
		}
	case "Cut", "Fade", "AlphaFade", "Merge", "Wipe", "WipeReverse", "Zoom", "CrossZoom", "Slide", "SlideReverse", "Fly", "FlyRotate",
		"Cube", "CubeZoom", "VerticalWipe", "VerticalWipeReverse", "VerticalSlide", "VerticalSlideReverse",
		"Transition1", "Transition2", "Transition3", "Transition4", "Stinger1", "Stinger2", "Stinger3", "Stinger4":
		if in != nil {
			mix := 1
			if m := q.Get("Mix"); m != "" {
				n, _ := strconv.Atoi(m)
				mix = n + 1 // vMix's Mix parameter is 0-based: 0 = main, 1 = Mix 2
			}
			f.active[mix] = in.Number
		}
	}
	w.WriteHeader(http.StatusOK)
	fmt.Fprint(w, "Function completed successfully.")
}

type xmlInput struct {
	Key           string  `xml:"key,attr"`
	Number        int     `xml:"number,attr"`
	Type          string  `xml:"type,attr"`
	Title         string  `xml:"title,attr"`
	ShortTitle    string  `xml:"shortTitle,attr"`
	State         string  `xml:"state,attr"`
	Position      int     `xml:"position,attr"`
	Duration      int     `xml:"duration,attr"`
	SelectedIndex int     `xml:"selectedIndex,attr"`
	Volume        float64 `xml:"volume,attr"`
	Text          string  `xml:",chardata"`
}

type xmlMix struct {
	Number  int `xml:"number,attr"`
	Preview int `xml:"preview"`
	Active  int `xml:"active"`
}

type xmlRoot struct {
	XMLName xml.Name   `xml:"vmix"`
	Version string     `xml:"version"`
	Edition string     `xml:"edition"`
	Inputs  []xmlInput `xml:"inputs>input"`
	Preview int        `xml:"preview"`
	Active  int        `xml:"active"`
	Mixes   []xmlMix   `xml:"mix"`
}

func (f *Fake) writeXML(w http.ResponseWriter) {
	root := xmlRoot{Version: "28.0.0.42 (fake)", Edition: "4K", Active: f.active[1], Preview: 1}
	for _, in := range f.inputs {
		state := "Paused"
		if in.Playing {
			state = "Running"
		}
		title := in.Title
		if in.Selected > 0 {
			parts := strings.Split(strings.ReplaceAll(in.Items[in.Selected-1], "\\", "/"), "/")
			title = in.Title + " - " + parts[len(parts)-1]
		}
		root.Inputs = append(root.Inputs, xmlInput{Key: in.Key, Number: in.Number, Type: in.Type, Title: title, ShortTitle: in.Title,
			State: state, Position: in.PosMs, Duration: in.DurMs, SelectedIndex: in.Selected, Volume: in.Volume, Text: title})
	}
	for n := 2; n <= 4; n++ {
		root.Mixes = append(root.Mixes, xmlMix{Number: n, Active: f.active[n], Preview: 1})
	}
	w.Header().Set("Content-Type", "text/xml")
	_ = xml.NewEncoder(w).Encode(root)
}
