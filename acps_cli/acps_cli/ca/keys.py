import logging
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)


def generate_private_key(key_type="ec"):
    logger.debug(f"Generating {key_type.upper()} private key")
    if key_type == "rsa":
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return ec.generate_private_key(ec.SECP256R1())


def save_private_key(key, path):
    # Ensure directory exists
    os.makedirs(os.path.dirname(path), exist_ok=True)
    logger.debug(f"Saving private key to {path}")

    with open(path, "wb") as f:
        f.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    # Set permissions to 600
    os.chmod(path, 0o600)


def load_private_key(path):
    logger.debug(f"Loading private key from {path}")
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def generate_csr(private_key, common_name, path):
    # Ensure directory exists
    os.makedirs(os.path.dirname(path), exist_ok=True)
    logger.debug(f"Generating CSR for CN={common_name} at {path}")

    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                ]
            )
        )
        .sign(private_key, hashes.SHA256())
    )

    with open(path, "wb") as f:
        f.write(csr.public_bytes(serialization.Encoding.PEM))

    return csr
