// Package pairing checks the codes in tablet pairing QR codes.
//
// "s1." codes are signed by the server (Ed25519) and checked with the public
// key from the manifest, so they work offline. "a1." codes are issued by the
// agent itself (HMAC) from the companion when the server can't be reached.
package pairing

import (
	"crypto/ed25519"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"time"
)

type Claims struct {
	Type  string `json:"typ"`
	Agent int64  `json:"agent"`
	Role  string `json:"role"`
	Name  string `json:"name"`
	Nonce string `json:"nonce"`
	Exp   int64  `json:"exp"`
}

var b64 = base64.RawURLEncoding

// Verify checks a pairing code and returns its claims.
func Verify(token, serverPubKeyB64 string, agentID int64, agentSecret string, now time.Time) (*Claims, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return nil, errors.New("this is not a Vitrine pairing code")
	}
	prefix, body, sig := parts[0], parts[1], parts[2]
	signature, err := b64.DecodeString(sig)
	if err != nil {
		return nil, errors.New("damaged pairing code")
	}
	switch prefix {
	case "s1":
		if serverPubKeyB64 == "" {
			return nil, errors.New("the agent hasn't received the server key yet; let it connect to the server once")
		}
		key, err := base64.StdEncoding.DecodeString(serverPubKeyB64)
		if err != nil || len(key) != ed25519.PublicKeySize {
			return nil, errors.New("server key is invalid")
		}
		if !ed25519.Verify(ed25519.PublicKey(key), []byte(body), signature) {
			return nil, errors.New("pairing code signature is wrong")
		}
	case "a1":
		mac := hmac.New(sha256.New, []byte(agentSecret))
		mac.Write([]byte(body))
		if !hmac.Equal(mac.Sum(nil), signature) {
			return nil, errors.New("pairing code signature is wrong")
		}
	default:
		return nil, errors.New("unknown pairing code version")
	}
	raw, err := b64.DecodeString(body)
	if err != nil {
		return nil, errors.New("damaged pairing code")
	}
	var claims Claims
	if err := json.Unmarshal(raw, &claims); err != nil {
		return nil, errors.New("damaged pairing code")
	}
	if claims.Type != "pair" {
		return nil, errors.New("not a pairing code")
	}
	if prefix == "s1" && agentID != 0 && claims.Agent != agentID {
		return nil, errors.New("this code is for another agent")
	}
	if now.Unix() > claims.Exp {
		return nil, errors.New("this pairing code has expired; make a new one")
	}
	if claims.Role != "back" && claims.Role != "pulpit" {
		return nil, errors.New("unknown tablet role")
	}
	return &claims, nil
}

// IssueLocal makes an agent-signed pairing code (used when the server is offline).
func IssueLocal(agentSecret string, role, name string, ttl time.Duration) string {
	nonce := make([]byte, 12)
	_, _ = rand.Read(nonce)
	claims := Claims{Type: "pair", Role: role, Name: name, Nonce: "local-" + hex.EncodeToString(nonce), Exp: time.Now().Add(ttl).Unix()}
	raw, _ := json.Marshal(claims)
	body := b64.EncodeToString(raw)
	mac := hmac.New(sha256.New, []byte(agentSecret))
	mac.Write([]byte(body))
	return "a1." + body + "." + b64.EncodeToString(mac.Sum(nil))
}

// NewSessionToken returns a random token for a tablet's cookie and its hash.
func NewSessionToken() (token, hash string) {
	buf := make([]byte, 32)
	_, _ = rand.Read(buf)
	token = b64.EncodeToString(buf)
	return token, HashToken(token)
}

func HashToken(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}
