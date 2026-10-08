import hashlib
import secrets
from threading import BoundedSemaphore

# OWASP scrypt configuration with a 32 MiB working set, using Python's OpenSSL.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 3
_hash_slots = BoundedSemaphore(2)
DUMMY_PASSWORD_HASH = f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$" + "00" * 16 + "$" + "00" * 32


def _derive(password: str, salt: bytes) -> bytes:
    with _hash_slots:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N,
                              r=SCRYPT_R, p=SCRYPT_P, maxmem=64 * 1024 * 1024, dklen=32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, digest = encoded.split("$")
        if (algorithm, int(n), int(r), int(p)) != ("scrypt", SCRYPT_N, SCRYPT_R, SCRYPT_P):
            return False
        salt_bytes, expected = bytes.fromhex(salt), bytes.fromhex(digest)
        if len(salt_bytes) != 16 or len(expected) != 32:
            return False
        return secrets.compare_digest(_derive(password, salt_bytes), expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
