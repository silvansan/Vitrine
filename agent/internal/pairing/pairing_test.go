package pairing

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"testing"
	"time"
)

func TestServerAndLocalCodes(t *testing.T) {
	pub, priv, _ := ed25519.GenerateKey(nil)
	claims := Claims{Type: "pair", Agent: 7, Role: "pulpit", Name: "Pulpit", Nonce: "abc", Exp: time.Now().Add(time.Minute).Unix()}
	raw, _ := json.Marshal(claims)
	body := b64.EncodeToString(raw)
	token := "s1." + body + "." + b64.EncodeToString(ed25519.Sign(priv, []byte(body)))
	pubB64 := base64.StdEncoding.EncodeToString(pub)

	got, err := Verify(token, pubB64, 7, "secret", time.Now())
	if err != nil || got.Role != "pulpit" || got.Nonce != "abc" {
		t.Fatalf("server code: %v %+v", err, got)
	}
	if _, err := Verify(token, pubB64, 8, "secret", time.Now()); err == nil {
		t.Fatal("code for another agent must fail")
	}
	if _, err := Verify(token, pubB64, 7, "secret", time.Now().Add(2*time.Minute)); err == nil {
		t.Fatal("expired code must fail")
	}
	if _, err := Verify(token[:len(token)-3]+"AAA", pubB64, 7, "secret", time.Now()); err == nil {
		t.Fatal("tampered code must fail")
	}

	local := IssueLocal("secret", "back", "Back", time.Minute)
	got, err = Verify(local, "", 0, "secret", time.Now())
	if err != nil || got.Role != "back" {
		t.Fatalf("local code: %v", err)
	}
	if _, err := Verify(local, "", 0, "other", time.Now()); err == nil {
		t.Fatal("local code with wrong secret must fail")
	}
}
