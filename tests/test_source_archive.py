import importlib.util
from pathlib import Path
import io,json,zipfile
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.exceptions import InvalidTag
spec=importlib.util.spec_from_file_location('archive',Path(__file__).parents[1]/'harness/archive_sources.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_private_archive_roundtrip_tamper_and_immutable(tmp_path):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    pub=tmp_path/'pub.pem';priv=tmp_path/'priv.pem'
    pub.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    priv.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    source=tmp_path/'private';source.mkdir();(source/'sample.json').write_text('{"cusip":"PRIVATE123"}')
    archive=tmp_path/'data.enc.json';m.seal(source,pub,archive)
    assert 'PRIVATE123' not in archive.read_text()
    with zipfile.ZipFile(io.BytesIO(m.unseal(archive,priv))) as z:
        assert z.read('sample.json')==(source/'sample.json').read_bytes()
    with pytest.raises(FileExistsError):m.seal(source,pub,archive)
    payload=json.loads(archive.read_text());payload['nonce']='AAAAAAAAAAAAAAAA';archive.write_text(json.dumps(payload))
    with pytest.raises(InvalidTag):m.unseal(archive,priv)
