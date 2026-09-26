from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

DEFAULT_HOSTS = ("localhost", "127.0.0.1")


@dataclass(frozen=True, slots=True)
class TlsMaterial:
    certificate_path: Path
    private_key_path: Path

    def load(self) -> tuple[bytes, bytes]:
        return self.private_key_path.read_bytes(), self.certificate_path.read_bytes()

    def fingerprint(self) -> str:
        return certificate_fingerprint(self.certificate_path.read_bytes())

    def hosts(self) -> tuple[str, ...]:
        return certificate_hosts(self.certificate_path.read_bytes())


def generate_self_signed(
    directory: Path, hosts: tuple[str, ...], *, regenerate: bool = False
) -> TlsMaterial:
    """Create the coordinator certificate, or return the existing one.

    With ``regenerate`` the key and certificate are replaced, which changes the
    fingerprint; nodes must then be given the new certificate or a new invite.
    Without it, an existing certificate is reused only if it already covers ``hosts``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    certificate_path = directory / "server.crt"
    private_key_path = directory / "server.key"
    material = TlsMaterial(certificate_path, private_key_path)
    if certificate_path.exists() and private_key_path.exists() and not regenerate:
        missing = [host for host in hosts if not covers(material.hosts(), host)]
        if missing:
            raise CertificateHostsError(missing, material.hosts())
        return material
    key = ec.generate_private_key(ec.SECP256R1())
    names: list[x509.GeneralName] = []
    for host in dict.fromkeys((*DEFAULT_HOSTS, *hosts)):
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
    key_bytes = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    certificate_bytes = certificate.public_bytes(serialization.Encoding.PEM)
    _replace(private_key_path, key_bytes)
    _replace(certificate_path, certificate_bytes)
    return material


class CertificateHostsError(Exception):
    def __init__(self, missing: list[str], present: tuple[str, ...]) -> None:
        super().__init__(
            f"coordinator certificate does not cover {', '.join(missing)} "
            f"(covers {', '.join(present)}); rerun with --regenerate"
        )
        self.missing = tuple(missing)
        self.present = present


def certificate_fingerprint(pem: bytes) -> str:
    certificate = x509.load_pem_x509_certificate(pem)
    return hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()


def certificate_hosts(pem: bytes) -> tuple[str, ...]:
    certificate = x509.load_pem_x509_certificate(pem)
    try:
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return ()
    hosts: list[str] = list(names.get_values_for_type(x509.DNSName))
    hosts.extend(str(address) for address in names.get_values_for_type(x509.IPAddress))
    return tuple(hosts)


def covers(hosts: tuple[str, ...], host: str) -> bool:
    try:
        wanted = ipaddress.ip_address(host)
    except ValueError:
        return host.lower() in {item.lower() for item in hosts}
    for item in hosts:
        try:
            if ipaddress.ip_address(item) == wanted:
                return True
        except ValueError:
            continue
    return False


def _replace(path: Path, content: bytes) -> None:
    import os
    import secrets

    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    temporary.write_bytes(content)
    if os.name != "nt" and path.suffix == ".key":
        temporary.chmod(0o600)
    os.replace(temporary, path)
