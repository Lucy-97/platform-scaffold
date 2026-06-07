package crypto

import "testing"

func TestRoundTrip(t *testing.T) {
	plaintext := "hello, world"
	masterKey := "test-master-key"

	ct, err := Encrypt(plaintext, masterKey)
	if err != nil {
		t.Fatal(err)
	}
	got, err := Decrypt(ct, masterKey)
	if err != nil {
		t.Fatal(err)
	}
	if got != plaintext {
		t.Fatalf("roundtrip mismatch: got %q want %q", got, plaintext)
	}
}

func TestWrongKey(t *testing.T) {
	ct, _ := Encrypt("data", "key-a")
	if _, err := Decrypt(ct, "key-b"); err == nil {
		t.Fatal("expected decrypt error with wrong key, got nil")
	}
}
