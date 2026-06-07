// Package crypto 提供 AES-256-GCM 加解密。
//
// 与 Python 端 crypto.py 的 _derive_key (SHA-256) + AESGCM 完全对齐，
// 这样 Go 加密的密文 Python 能解，反之亦然。
//
// 密文格式: base64(nonce_12 + ciphertext + tag_16)
package crypto

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"fmt"
)

// deriveKey 用 SHA-256 把任意长度 masterKey 派生为 32 字节 AES 密钥。
func deriveKey(masterKey string) []byte {
	h := sha256.Sum256([]byte(masterKey))
	return h[:]
}

// Encrypt 加密明文，返回 Base64 密文。
func Encrypt(plaintext, masterKey string) (string, error) {
	key := deriveKey(masterKey)
	block, err := aes.NewCipher(key)
	if err != nil {
		return "", fmt.Errorf("aes: %w", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", fmt.Errorf("gcm: %w", err)
	}
	nonce := make([]byte, 12)
	if _, err := rand.Read(nonce); err != nil {
		return "", fmt.Errorf("nonce: %w", err)
	}
	ct := gcm.Seal(nil, nonce, []byte(plaintext), nil)
	combined := make([]byte, 0, len(nonce)+len(ct))
	combined = append(combined, nonce...)
	combined = append(combined, ct...)
	return base64.StdEncoding.EncodeToString(combined), nil
}

// Decrypt 解密 Base64 密文，返回明文字符串。
func Decrypt(ciphertextB64, masterKey string) (string, error) {
	raw, err := base64.StdEncoding.DecodeString(ciphertextB64)
	if err != nil {
		return "", fmt.Errorf("base64: %w", err)
	}
	if len(raw) < 28 {
		return "", fmt.Errorf("ciphertext too short: %d", len(raw))
	}
	nonce := raw[:12]
	ct := raw[12:]
	key := deriveKey(masterKey)
	block, err := aes.NewCipher(key)
	if err != nil {
		return "", fmt.Errorf("aes: %w", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", fmt.Errorf("gcm: %w", err)
	}
	pt, err := gcm.Open(nil, nonce, ct, nil)
	if err != nil {
		return "", fmt.Errorf("decrypt: 密钥不匹配或密文损坏")
	}
	return string(pt), nil
}
