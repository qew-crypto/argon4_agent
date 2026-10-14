import base64
import json
import os
import unittest
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from agent.authcrypto import EncryptedLogin
except ImportError:
    EncryptedLogin = None


def envelope(metadata, code='LOCAL_TEST_CODE'):
    public = serialization.load_der_public_key(base64.b64decode(metadata['public_key']))
    key, iv = os.urandom(32), os.urandom(12)
    data = json.dumps({'code': code, 'challenge': metadata['challenge']}).encode()
    cipher = AESGCM(key).encrypt(iv, data, ('agent-auth-v1:'+metadata['challenge']).encode())
    wrapped = public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                          algorithm=hashes.SHA256(), label=None))
    return {k:base64.b64encode(v).decode() for k,v in {'key':wrapped,'iv':iv,'ciphertext':cipher}.items()}


@unittest.skipIf(EncryptedLogin is None, 'install cryptography')
class CryptoTests(unittest.TestCase):
    def test_roundtrip_and_replay_rejected(self):
        ctx=EncryptedLogin();data=envelope(ctx.public())
        self.assertNotIn('LOCAL_TEST_CODE', json.dumps(data))
        self.assertEqual(ctx.decrypt(data),'LOCAL_TEST_CODE')
        with self.assertRaises(ValueError):ctx.decrypt(data)

    def test_modified_ciphertext_rejected(self):
        ctx=EncryptedLogin();data=envelope(ctx.public());cipher=bytearray(base64.b64decode(data['ciphertext']))
        cipher[0]^=1;data['ciphertext']=base64.b64encode(cipher).decode()
        with self.assertRaises(ValueError):ctx.decrypt(data)
        self.assertFalse(ctx.used)

    def test_other_session_rejected(self):
        ctx=EncryptedLogin();other=EncryptedLogin()
        with self.assertRaises(ValueError):other.decrypt(envelope(ctx.public()))

    def test_long_code_hybrid_encryption(self):
        ctx=EncryptedLogin();code='X'*4096
        self.assertEqual(ctx.decrypt(envelope(ctx.public(),code)),code)
