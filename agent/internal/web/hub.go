package web

import (
	"context"
	"encoding/json"
	"sync"
	"time"

	"github.com/coder/websocket"
)

type client struct {
	conn     *websocket.Conn
	screenID string
	send     chan []byte
}

// Hub fans messages out to every connected tablet and companion page.
type Hub struct {
	mu      sync.Mutex
	clients map[*client]struct{}
}

func newHub() *Hub { return &Hub{clients: map[*client]struct{}{}} }

func (h *Hub) add(c *client) {
	h.mu.Lock()
	h.clients[c] = struct{}{}
	h.mu.Unlock()
}

func (h *Hub) remove(c *client) {
	h.mu.Lock()
	delete(h.clients, c)
	h.mu.Unlock()
}

func (h *Hub) count() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.clients)
}

// Broadcast sends msg to everyone. Slow clients drop messages rather than block.
func (h *Hub) Broadcast(msg any) {
	data, err := json.Marshal(msg)
	if err != nil {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	for c := range h.clients {
		select {
		case c.send <- data:
		default:
		}
	}
}

// SendTo sends msg to the tablets of one screen.
func (h *Hub) SendTo(screenID string, msg any) {
	data, err := json.Marshal(msg)
	if err != nil {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	for c := range h.clients {
		if c.screenID == screenID {
			select {
			case c.send <- data:
			default:
			}
		}
	}
}

// CloseAll ends every connection (agent restart); clients reconnect by themselves.
func (h *Hub) CloseAll() {
	h.mu.Lock()
	defer h.mu.Unlock()
	for c := range h.clients {
		c.conn.Close(websocket.StatusGoingAway, "agent restarting")
	}
}

// Disconnect closes the connections of a screen (after unpairing).
func (h *Hub) Disconnect(screenID string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	for c := range h.clients {
		if c.screenID == screenID {
			c.conn.Close(4401, "unpaired")
		}
	}
}

func (h *Hub) serve(ctx context.Context, c *client, first []byte) {
	h.add(c)
	defer h.remove(c)
	defer c.conn.CloseNow()
	if first != nil {
		_ = c.conn.Write(ctx, websocket.MessageText, first)
	}
	readCtx := c.conn.CloseRead(ctx)
	ping := time.NewTicker(20 * time.Second)
	defer ping.Stop()
	for {
		select {
		case <-readCtx.Done():
			return
		case data := <-c.send:
			writeCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
			err := c.conn.Write(writeCtx, websocket.MessageText, data)
			cancel()
			if err != nil {
				return
			}
		case <-ping.C:
			pingCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
			err := c.conn.Ping(pingCtx)
			cancel()
			if err != nil {
				return
			}
		}
	}
}
