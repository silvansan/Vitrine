package web

import (
	"encoding/json"
	"testing"

	"github.com/silvansan/vitrine/agent/internal/model"
	"github.com/silvansan/vitrine/agent/internal/store"
)

// The team answers a guest's request from outside the tablets (a Telegram button, via the server).
func TestDeskControl(t *testing.T) {
	st, err := store.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	_ = st.Update(func(s *store.State) {
		s.Manifest = &model.Manifest{Sets: []model.Set{{ID: 4, ServiceID: 1, Guest: "Dan"}}}
	})
	s := &Server{Store: st}
	s.requests = map[int64]accessRequest{4: {SetID: 4, Guest: "Dan"}}
	run := func(cmd map[string]any) error {
		raw, _ := json.Marshal(cmd)
		return s.ExecuteRemote(raw)
	}
	has := func() bool {
		ok := false
		st.Read(func(x *store.State) { _, ok = activeControls(x)[4] })
		return ok
	}

	if err := run(map[string]any{"action": "desk_control", "set_id": 4, "on": true, "by": "Ana in Telegram"}); err != nil {
		t.Fatal(err)
	}
	if !has() || len(s.requestsView()) != 0 {
		t.Fatal("allowing should give control and answer the request")
	}
	// Refusing an old request doesn't take back control given meanwhile.
	if err := run(map[string]any{"action": "desk_control", "set_id": 4, "on": false}); err == nil || !has() {
		t.Fatalf("refuse after control was given should be refused itself (err=%v, control=%v)", err, has())
	}
	if err := run(map[string]any{"action": "desk_control", "set_id": 99, "on": true}); err == nil {
		t.Fatal("unknown set must fail")
	}
}
