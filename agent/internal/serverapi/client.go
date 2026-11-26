// Package serverapi talks to the Vitrine server over Tailscale.
package serverapi

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"

	"github.com/silvansan/vitrine/agent/internal/model"
)

type Client struct {
	BaseURL string
	Token   string
	HTTP    *http.Client
	// Download HTTP client: no overall timeout, big files take a while.
	Bulk *http.Client
	// OnCommand receives commands the server relays from a guest's phone (see app.handleRemote).
	OnCommand func(raw json.RawMessage)

	outMu sync.Mutex
	out   chan []byte
}

// Send queues a message for the server over the WebSocket (dropped while disconnected).
func (c *Client) Send(v any) {
	data, err := json.Marshal(v)
	if err != nil {
		return
	}
	c.outMu.Lock()
	out := c.out
	c.outMu.Unlock()
	if out == nil {
		return
	}
	select {
	case out <- data:
	default:
	}
}

func New(baseURL, token string) *Client {
	return &Client{
		BaseURL: strings.TrimRight(baseURL, "/"),
		Token:   token,
		HTTP:    &http.Client{Timeout: 20 * time.Second},
		Bulk:    &http.Client{},
	}
}

func (c *Client) request(ctx context.Context, method, path string, body any) (*http.Request, error) {
	var reader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		reader = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.BaseURL+path, reader)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.Token)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	return req, nil
}

func (c *Client) doJSON(ctx context.Context, method, path string, body, out any) error {
	req, err := c.request(ctx, method, path, body)
	if err != nil {
		return err
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusUnauthorized {
		return errors.New("the server refused the agent token; paste it again under Settings > Connection")
	}
	if resp.StatusCode/100 != 2 {
		data, _ := io.ReadAll(io.LimitReader(resp.Body, 500))
		return fmt.Errorf("%s %s: %s %s", method, path, resp.Status, strings.TrimSpace(string(data)))
	}
	if out == nil {
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// Manifest fetches the current manifest.
func (c *Client) Manifest(ctx context.Context) (*model.Manifest, error) {
	var m model.Manifest
	if err := c.doJSON(ctx, http.MethodGet, "/api/agent/manifest", nil, &m); err != nil {
		return nil, err
	}
	return &m, nil
}

// Heartbeat reports status; the server answers with its manifest version.
func (c *Client) Heartbeat(ctx context.Context, payload any) (int, error) {
	var out struct {
		ManifestVersion int `json:"manifest_version"`
	}
	err := c.doJSON(ctx, http.MethodPost, "/api/agent/heartbeat", payload, &out)
	return out.ManifestVersion, err
}

// Pairing asks the server for a signed pairing code.
func (c *Client) Pairing(ctx context.Context, role, name string) (string, error) {
	var out struct {
		Token string `json:"token"`
	}
	err := c.doJSON(ctx, http.MethodPost, "/api/agent/pairing", map[string]string{"role": role, "name": name}, &out)
	return out.Token, err
}

// Download fetches path into dest, resuming from dest+".part" if present.
// wrap lets the caller throttle the body reader.
func (c *Client) Download(ctx context.Context, path, dest string, wrap func(io.Reader) io.Reader) error {
	part := dest + ".part"
	var offset int64
	if info, err := os.Stat(part); err == nil {
		offset = info.Size()
	}
	req, err := c.request(ctx, http.MethodGet, path, nil)
	if err != nil {
		return err
	}
	if offset > 0 {
		req.Header.Set("Range", fmt.Sprintf("bytes=%d-", offset))
	}
	resp, err := c.Bulk.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	flags := os.O_CREATE | os.O_WRONLY
	switch resp.StatusCode {
	case http.StatusPartialContent:
		flags |= os.O_APPEND
	case http.StatusOK:
		flags |= os.O_TRUNC
	case http.StatusRequestedRangeNotSatisfiable:
		_ = os.Remove(part)
		return errors.New("partial file was larger than the source; restarting")
	default:
		return fmt.Errorf("download %s: %s", path, resp.Status)
	}
	file, err := os.OpenFile(part, flags, 0o644)
	if err != nil {
		return err
	}
	var body io.Reader = resp.Body
	if wrap != nil {
		body = wrap(body)
	}
	_, copyErr := io.Copy(file, body)
	closeErr := file.Close()
	if copyErr != nil {
		return copyErr
	}
	if closeErr != nil {
		return closeErr
	}
	_ = os.Remove(dest)
	return os.Rename(part, dest)
}

// Listen keeps a WebSocket open and calls onChange whenever the server says
// the manifest changed (and once after every connect). It returns when ctx ends.
func (c *Client) Listen(ctx context.Context, onChange func(), onState func(connected bool)) {
	wsURL := strings.Replace(strings.Replace(c.BaseURL, "https://", "wss://", 1), "http://", "ws://", 1) + "/ws/agent/"
	backoff := time.Second
	for ctx.Err() == nil {
		dialCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
		conn, _, err := websocket.Dial(dialCtx, wsURL, &websocket.DialOptions{
			HTTPHeader: http.Header{"Authorization": []string{"Bearer " + c.Token}},
		})
		cancel()
		if err != nil {
			onState(false)
			log.Printf("server websocket: %v (retry in %s)", err, backoff)
			select {
			case <-ctx.Done():
				return
			case <-time.After(backoff):
			}
			if backoff < 30*time.Second {
				backoff *= 2
			}
			continue
		}
		backoff = time.Second
		onState(true)
		c.readLoop(ctx, conn, onChange)
		onState(false)
	}
}

func (c *Client) readLoop(ctx context.Context, conn *websocket.Conn, onChange func()) {
	defer conn.CloseNow()
	pingCtx, stopPing := context.WithCancel(ctx)
	defer stopPing()
	out := make(chan []byte, 32)
	c.outMu.Lock()
	c.out = out
	c.outMu.Unlock()
	defer func() {
		c.outMu.Lock()
		c.out = nil
		c.outMu.Unlock()
	}()
	go func() {
		for {
			select {
			case <-pingCtx.Done():
				return
			case data := <-out:
				_ = conn.Write(pingCtx, websocket.MessageText, data)
			}
		}
	}()
	go func() {
		ticker := time.NewTicker(25 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-pingCtx.Done():
				return
			case <-ticker.C:
				_ = wsjson.Write(pingCtx, conn, map[string]string{"type": "ping"})
			}
		}
	}()
	for {
		var raw json.RawMessage
		if err := wsjson.Read(ctx, conn, &raw); err != nil {
			if ctx.Err() == nil {
				log.Printf("server websocket closed: %v", err)
			}
			return
		}
		var msg struct {
			Type string `json:"type"`
		}
		_ = json.Unmarshal(raw, &msg)
		switch msg.Type {
		case "hello", "manifest":
			onChange()
		case "command":
			if c.OnCommand != nil {
				go c.OnCommand(raw)
			}
		}
	}
}

// NewMarker is a marker added on a tablet.
type NewMarker struct {
	AssetID int64  `json:"asset_id"`
	TMs     int    `json:"t_ms"`
	Label   string `json:"label"`
	By      string `json:"by"`
}

// Markers sends added and deleted markers to the server.
func (c *Client) Markers(ctx context.Context, add []NewMarker, del []int64) error {
	if add == nil {
		add = []NewMarker{}
	}
	if del == nil {
		del = []int64{}
	}
	return c.doJSON(ctx, http.MethodPost, "/api/agent/markers", map[string]any{"add": add, "delete": del}, nil)
}

// Clip changes a set entry's mark in / mark out on the server (back tablet). The body is
// sent as given (see web.ClipEdit); the server's own explanation is returned as the error.
func (c *Client) Clip(ctx context.Context, edit any) error {
	req, err := c.request(ctx, http.MethodPost, "/api/agent/clips", edit)
	if err != nil {
		return err
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	var out struct {
		OK    bool   `json:"ok"`
		Error string `json:"error"`
	}
	_ = json.NewDecoder(io.LimitReader(resp.Body, 4096)).Decode(&out)
	if resp.StatusCode == http.StatusNotFound && out.Error == "" {
		return errors.New("the server is too old for clips; update it")
	}
	if resp.StatusCode/100 != 2 {
		if out.Error != "" {
			return errors.New(out.Error)
		}
		return fmt.Errorf("saving the clip failed: %s", resp.Status)
	}
	return nil
}
