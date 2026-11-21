// Package vmix is a small client for the vMix web API (port 8088).
package vmix

import (
	"context"
	"encoding/xml"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type Input struct {
	Key           string `json:"key"`
	Number        int    `json:"number"`
	Type          string `json:"type"`
	Title         string `json:"title"`
	ShortTitle    string `json:"short_title"`
	State         string `json:"state"`
	Position      int    `json:"position"`
	Duration      int    `json:"duration"`
	SelectedIndex int    `json:"selected_index"`
	// Audio volume 0–100 as vMix shows it.
	Volume float64 `json:"volume"`
}

// Name is the input's own name, without the file name vMix appends for some types.
func (i Input) Name() string {
	if i.ShortTitle != "" {
		return i.ShortTitle
	}
	return i.Title
}

type State struct {
	Version string
	Inputs  []Input
	// Active input number per mix: 1 = main output, 2.. = Mix 2..
	Active map[int]int
}

// InputByName finds an input by its name (short title), then by title prefix.
func (s *State) InputByName(name string) *Input {
	if s == nil || name == "" {
		return nil
	}
	for i := range s.Inputs {
		if strings.EqualFold(s.Inputs[i].ShortTitle, name) || strings.EqualFold(s.Inputs[i].Title, name) {
			return &s.Inputs[i]
		}
	}
	for i := range s.Inputs {
		if strings.HasPrefix(strings.ToLower(s.Inputs[i].Title), strings.ToLower(name)+" ") {
			return &s.Inputs[i]
		}
	}
	return nil
}

func (s *State) InputByKey(key string) *Input {
	if s == nil || key == "" {
		return nil
	}
	for i := range s.Inputs {
		if s.Inputs[i].Key == key {
			return &s.Inputs[i]
		}
	}
	return nil
}

func (s *State) InputByNumber(n int) *Input {
	if s == nil {
		return nil
	}
	for i := range s.Inputs {
		if s.Inputs[i].Number == n {
			return &s.Inputs[i]
		}
	}
	return nil
}

// Mixes returns the mix numbers vMix reports (1 = main).
func (s *State) Mixes() []int {
	out := []int{1}
	for n := 2; n <= 16; n++ {
		if _, ok := s.Active[n]; ok {
			out = append(out, n)
		}
	}
	return out
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
}

type xmlMix struct {
	Number int `xml:"number,attr"`
	Active int `xml:"active"`
}

type xmlVMix struct {
	Version string     `xml:"version"`
	Inputs  []xmlInput `xml:"inputs>input"`
	Active  int        `xml:"active"`
	Mixes   []xmlMix   `xml:"mix"`
}

// Parse reads vMix's XML state.
func Parse(data []byte) (*State, error) {
	var raw xmlVMix
	if err := xml.Unmarshal(data, &raw); err != nil {
		return nil, err
	}
	st := &State{Version: raw.Version, Active: map[int]int{1: raw.Active}}
	for _, in := range raw.Inputs {
		st.Inputs = append(st.Inputs, Input(in))
	}
	for _, mix := range raw.Mixes {
		if mix.Number >= 2 {
			st.Active[mix.Number] = mix.Active
		}
	}
	return st, nil
}

type Client struct {
	BaseURL string
	HTTP    *http.Client
}

func New(baseURL string) *Client {
	return &Client{BaseURL: strings.TrimRight(baseURL, "/"), HTTP: &http.Client{Timeout: 3 * time.Second}}
}

// State reads the current XML state.
func (c *Client) State(ctx context.Context) (*State, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+"/api", nil)
	if err != nil {
		return nil, err
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("vMix API: %s", resp.Status)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	if err != nil {
		return nil, err
	}
	return Parse(data)
}

// Call runs one vMix function, e.g. Call(ctx, "Fade", {"Input": "GM_A", "Duration": "500"}).
func (c *Client) Call(ctx context.Context, function string, params map[string]string) error {
	q := url.Values{}
	q.Set("Function", function)
	for k, v := range params {
		if v != "" {
			q.Set(k, v)
		}
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+"/api/?"+q.Encode(), nil)
	if err != nil {
		return err
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("vMix %s: %s", function, resp.Status)
	}
	return nil
}
