package terminal

import (
	"crypto/ed25519"
	"encoding/base64"
	"testing"
)

func TestVerifyAuthorizedSignature_ed25519(t *testing.T) {
	pub, priv, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	line, err := SSHPublicKeyLine(pub, "test")
	if err != nil {
		t.Fatal(err)
	}
	id := "abc123"
	nonce := []byte("nonce-bytes-for-test-challenge!!")
	msg := BuildChallengeMessage(id, nonce)
	sig := ed25519.Sign(priv, msg)
	ok, err := VerifyAuthorizedSignature([]string{line}, msg, sig)
	if err != nil {
		t.Fatal(err)
	}
	if !ok {
		t.Fatal("expected signature to verify")
	}
	wrong := ed25519.Sign(priv, []byte("other message"))
	ok, err = VerifyAuthorizedSignature([]string{line}, msg, wrong)
	if err != nil {
		t.Fatal(err)
	}
	if ok {
		t.Fatal("expected wrong signature to fail")
	}
}

func TestBuildChallengeMessage_stable(t *testing.T) {
	nonce := []byte{1, 2, 3}
	got := string(BuildChallengeMessage("deadbeef", nonce))
	want := challengePrefix + "deadbeef:" + base64.StdEncoding.EncodeToString(nonce)
	if got != want {
		t.Fatalf("got %q want %q", got, want)
	}
}
