import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ssu_lecture_downloader.auth import decrypt_canvas_token
from ssu_lecture_downloader.errors import LoginFlowError


def test_decrypt_canvas_token_with_page_private_key():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    encrypted = public_key.encrypt(b"decrypted-login-token", padding.PKCS1v15())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    result = decrypt_canvas_token(base64.b64encode(encrypted).decode(), private_pem)

    assert result == "decrypted-login-token"


def test_decrypt_canvas_token_classifies_invalid_key_or_payload():
    with pytest.raises(LoginFlowError) as exc:
        decrypt_canvas_token("not-base64", "not-a-private-key")

    assert exc.value.code == "token_decryption_failed"
