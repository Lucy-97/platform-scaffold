# crypto — AES-256-GCM 加解密

## 概述

对称加密服务，用于 `system_config` 表中 `encrypted=1` 的敏感配置项。Go 与 Python 端使用相同的密钥派生算法（SHA-256），密文格式完全互通。

## 密文格式

```
Base64( nonce_12字节 + ciphertext + tag_16字节 )
```

- `nonce`：每次加密随机生成 12 字节
- `ciphertext`：AES-256-GCM 密文（含 16 字节认证 tag）
- 相同明文 + 相同密钥，每次加密结果不同（nonce 不同）

## API

```go
// 加密
ciphertext, err := crypto.Encrypt("my-api-key-value", masterKey)

// 解密
plaintext, err := crypto.Decrypt(ciphertext, masterKey)
```

## 密钥派生

```
masterKey (任意长度字符串)
    │
    ▼ SHA-256
AES-256 密钥 (32 字节)
```

Go 端 `crypto.sha256.Sum256` 与 Python 端 `hashlib.sha256` 输出完全一致。

### Python 对齐代码

```python
from cryptography.hazmat.primitives.kges.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hashes import Hash
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import base64

def _derive_key(master_key: str) -> bytes:
    digest = Hash(SHA256())
    digest.update(master_key.encode())
    return digest.finalize()

def encrypt(plaintext: str, master_key: str) -> str:
    key = _derive_key(master_key)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()

def decrypt(cipher_b64: str, master_key: str) -> str:
    raw = base64.b64decode(cipher_b64)
    nonce, ct = raw[:12], raw[12:]
    key = _derive_key(master_key)
    return AESGCM(key).decrypt(nonce, ct, None).decode()
```

## 注意事项

- **masterKey 必须通过环境变量注入**（`CONFIG_MASTER_KEY`），不要硬编码
- masterKey 为空时 `dynconfig` 跳过加密配置加载，服务仍可启动（降级）
- 更换 masterKey 后，已加密的旧密文无法解密——需要先解密再重新加密，或通过 Admin 后台重写
- 密文长度 = Base64((明文长度) + 12 + 16) ≈ 明文 × 1.33 + 40
