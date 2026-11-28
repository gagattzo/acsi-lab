"""Encrypt source snapshots for private audit; only the offline owner key decrypts.

AES-256-GCM payload, random key wrapped with RSA-OAEP-SHA256. Public key alone
lets scheduled collectors append archives without a decryption secret in CI.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AAD = b'AISTRESS-private-source-archive-v1'

def seal(folder, public_key, output):
    folder, output = Path(folder), Path(output)
    files = sorted(f for f in folder.rglob('*') if f.is_file() and not f.is_symlink())
    if not files:
        raise ValueError('No source files to preserve')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.writestr(f.relative_to(folder).as_posix(), f.read_bytes())
    raw = buffer.getvalue()
    key, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
    pub = serialization.load_pem_public_key(Path(public_key).read_bytes())
    wrapped = pub.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    enc = lambda x: base64.b64encode(x).decode('ascii')
    payload = {'format': AAD.decode(), 'key': enc(wrapped), 'nonce': enc(nonce),
               'ciphertext': enc(AESGCM(key).encrypt(nonce, raw, AAD))}
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, sort_keys=True)+'\n').encode()
    if output.exists():
        raise FileExistsError('Archive is immutable; choose a new path')
    output.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()

def unseal(archive, private_key):
    data = json.loads(Path(archive).read_text())
    if data['format'] != AAD.decode():
        raise ValueError('Unknown archive format')
    dec = base64.b64decode
    priv = serialization.load_pem_private_key(Path(private_key).read_bytes(), password=None)
    key = priv.decrypt(dec(data['key']), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return AESGCM(key).decrypt(dec(data['nonce']), dec(data['ciphertext']), AAD)

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', default='private_data')
    p.add_argument('--public-key', default='config/archive_public.pem')
    p.add_argument('--output', required=True)
    a = p.parse_args()
    print('Encrypted source archive SHA256:', seal(a.input, a.public_key, a.output))
