import base64
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

def rsa_encrypt(message: str, public_key_pem: str) -> str:
    """
    使用 RSA 公钥加密字符串 (PKCS1_v1_5 填充)
    对应前端 JSEncrypt 逻辑
    """
    try:
        key = RSA.importKey(public_key_pem)
        cipher = PKCS1_v1_5.new(key)
        ciphertext = cipher.encrypt(message.encode('utf-8'))
        return base64.b64encode(ciphertext).decode('utf-8')
    except Exception as e:
        raise ValueError(f"Encryption failed: {e}")