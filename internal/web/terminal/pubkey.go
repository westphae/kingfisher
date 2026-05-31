package terminal

import (
	"crypto"
	"crypto/ecdsa"
	"crypto/ed25519"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"strings"

	"golang.org/x/crypto/ssh"
)

const challengePrefix = "kingfisher-terminal-v1:"

// BuildChallengeMessage returns the exact bytes the client must sign.
func BuildChallengeMessage(id string, nonce []byte) []byte {
	return []byte(challengePrefix + id + ":" + base64.StdEncoding.EncodeToString(nonce))
}

// VerifyAuthorizedSignature checks sig against message using configured keys.
func VerifyAuthorizedSignature(authorizedKeys []string, message, sig []byte) (bool, error) {
	if len(message) == 0 || len(sig) == 0 {
		return false, nil
	}
	for _, line := range authorizedKeys {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		pub, _, _, _, err := ssh.ParseAuthorizedKey([]byte(line))
		if err != nil {
			continue
		}
		if verifyPubKeySignature(pub, message, sig) {
			return true, nil
		}
	}
	return false, nil
}

func verifyPubKeySignature(pub ssh.PublicKey, message, sig []byte) bool {
	cryptoPub, ok := pub.(ssh.CryptoPublicKey)
	if !ok {
		return false
	}
	switch pk := cryptoPub.CryptoPublicKey().(type) {
	case ed25519.PublicKey:
		return ed25519.Verify(pk, message, sig)
	case *rsa.PublicKey:
		hash := sha256.Sum256(message)
		return rsa.VerifyPKCS1v15(pk, crypto.SHA256, hash[:], sig) == nil
	case *ecdsa.PublicKey:
		hash := sha256.Sum256(message)
		return ecdsa.VerifyASN1(pk, hash[:], sig)
	default:
		return false
	}
}

// SSHPublicKeyLine encodes raw Ed25519 public key bytes as an authorized_keys line.
func SSHPublicKeyLine(rawPub []byte, comment string) (string, error) {
	if len(rawPub) != ed25519.PublicKeySize {
		return "", fmt.Errorf("terminal: ed25519 public key must be %d bytes", ed25519.PublicKeySize)
	}
	pub, err := ssh.NewPublicKey(ed25519.PublicKey(rawPub))
	if err != nil {
		return "", err
	}
	if comment == "" {
		comment = "kingfisher-terminal"
	}
	return strings.TrimSpace(string(ssh.MarshalAuthorizedKey(pub)) + " " + comment), nil
}
