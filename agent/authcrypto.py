"""Ephemeral, per-login hybrid encryption for OAuth codes. No persistent private key."""
import base64
import json
import secrets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class EncryptedLogin:
    def __init__(self):
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        self.challenge = secrets.token_urlsafe(32)
        self.used = False

    def public(self):
        der = self.private.public_key().public_bytes(serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        return {'public_key': base64.b64encode(der).decode(), 'challenge': self.challenge,
                'protocol': 'agent-auth-v1'}

    def decrypt(self, envelope):
        if self.used or not isinstance(envelope, dict):
            raise ValueError('Encrypted input rejected')
        try:
            wrapped = base64.b64decode(envelope['key'], validate=True)
            iv = base64.b64decode(envelope['iv'], validate=True)
            encrypted = base64.b64decode(envelope['ciphertext'], validate=True)
            if len(wrapped) != 384 or len(iv) != 12 or not 16 <= len(encrypted) <= 8192:
                raise ValueError()
            key = self.private.decrypt(wrapped, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                algorithm=hashes.SHA256(), label=None))
            if len(key) != 32:
                raise ValueError()
            plain = AESGCM(key).decrypt(iv, encrypted, ('agent-auth-v1:' + self.challenge).encode())
            payload = json.loads(plain)
            if payload.get('challenge') != self.challenge or not isinstance(payload.get('code'), str):
                raise ValueError()
            code = payload['code']
            if not code or len(code) > 4096 or not code.isascii() or any(ord(c) < 32 for c in code):
                raise ValueError()
            self.used = True
            return code
        except Exception:
            raise ValueError('Encrypted input rejected') from None
