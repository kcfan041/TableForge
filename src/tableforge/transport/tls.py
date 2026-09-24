from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


@dataclass(frozen=True, slots=True)
class TlsMaterial:
    certificate_path: Path
    private_key_path: Path

    def load(self) -> tuple[bytes, bytes]:
        return self.private_key_path.read_bytes(), self.certificate_path.read_bytes()


def generate_self_signed(directory: Path, hosts: tuple[str, ...]) -> TlsMaterial:
    directory.mkdir(parents=True, exist_ok=True)
    certificate_path = directory / "server.crt"
    private_key_path = directory / "server.key"
    if certificate_path.exists() and private_key_path.exists():
        return TlsMaterial(certificate_path, private_key_path)
    key = ec.generate_private_key(ec.SECP256R1())
    names: list[x509.GeneralName] = []
    for host in dict.fromkeys(("localhost", "127.0.0.1", *hosts)):
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            names.append(x509.DNSName(host))
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "tableforge-coordinator")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    private_key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return TlsMaterial(certificate_path, private_key_path)
