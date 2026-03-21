"""
Carryall Vault Encryption — age-x25519 encryption at rest for SLOS documents.

Encrypts document bodies before they hit disk, decrypts on read.
Frontmatter stays plaintext so metadata queries continue to work.

Encryption is applied based on vault sensitivity:
  - restricted (health, meta) → always encrypted
  - confidential (finance, family) → always encrypted
  - internal (community, personal, startup) → plaintext

Key management:
  - One age x25519 keypair per vault domain
  - Keys stored in {keys_dir}/vault-encryption/{domain}.key (identity)
  - Public keys stored in {keys_dir}/vault-encryption/{domain}.pub
"""

import logging
from pathlib import Path
from typing import Optional

import pyrage
from pyrage import x25519

logger = logging.getLogger(__name__)

# Sensitivity levels that require encryption
ENCRYPTED_SENSITIVITIES = {"restricted", "confidential"}

# Default sensitivity per vault domain
VAULT_SENSITIVITY = {
    "health": "restricted",
    "meta": "restricted",
    "finance": "confidential",
    "family": "confidential",
    "personal": "internal",
    "community": "internal",
    "startup": "internal",
}

AGE_HEADER = "-----BEGIN AGE ENCRYPTED FILE-----"
AGE_ARMOR_PREFIX = "age-encryption.org/v1"


def _encryption_keys_dir(keys_dir: Path) -> Path:
    """Return the vault-encryption subdirectory within the keys directory."""
    return keys_dir / "vault-encryption"


def generate_vault_keypair(keys_dir: Path, domain: str) -> tuple[str, str]:
    """
    Generate an age x25519 keypair for a vault domain.

    Args:
        keys_dir: Base keys directory (e.g. ~/slos/config/carryall/keys/)
        domain: Vault domain name (e.g. "health", "finance")

    Returns:
        Tuple of (public_key_str, identity_str)
    """
    enc_dir = _encryption_keys_dir(keys_dir)
    enc_dir.mkdir(parents=True, exist_ok=True)

    identity = x25519.Identity.generate()
    recipient = identity.to_public()

    identity_str = str(identity)
    public_str = str(recipient)

    # Write identity (secret key)
    key_path = enc_dir / f"{domain}.key"
    key_path.write_text(identity_str + "\n")
    key_path.chmod(0o600)

    # Write public key
    pub_path = enc_dir / f"{domain}.pub"
    pub_path.write_text(public_str + "\n")
    pub_path.chmod(0o644)

    logger.info(f"Generated vault encryption keypair for {domain}: {public_str}")
    return public_str, identity_str


def _load_recipient(keys_dir: Path, domain: str) -> x25519.Recipient:
    """Load the age public key (recipient) for a vault domain."""
    pub_path = _encryption_keys_dir(keys_dir) / f"{domain}.pub"
    if not pub_path.exists():
        raise FileNotFoundError(
            f"No encryption public key for vault '{domain}' at {pub_path}. "
            f"Generate one with: carryall vault generate-encryption-key {domain}"
        )
    public_str = pub_path.read_text().strip()
    return x25519.Recipient.from_str(public_str)


def _load_identity(keys_dir: Path, domain: str) -> x25519.Identity:
    """Load the age secret key (identity) for a vault domain."""
    key_path = _encryption_keys_dir(keys_dir) / f"{domain}.key"
    if not key_path.exists():
        raise FileNotFoundError(
            f"No encryption secret key for vault '{domain}' at {key_path}. "
            f"Generate one with: carryall vault generate-encryption-key {domain}"
        )
    identity_str = key_path.read_text().strip()
    return x25519.Identity.from_str(identity_str)


def is_encrypted(content: str) -> bool:
    """Check if document content is age-encrypted."""
    stripped = content.strip()
    return stripped.startswith(AGE_HEADER) or stripped.startswith(AGE_ARMOR_PREFIX)


def should_encrypt(domain: str, sensitivity: Optional[str] = None) -> bool:
    """
    Determine if a document should be encrypted based on domain and sensitivity.

    Args:
        domain: Vault domain name
        sensitivity: Document sensitivity level (falls back to domain default)
    """
    effective_sensitivity = sensitivity or VAULT_SENSITIVITY.get(domain, "internal")
    return effective_sensitivity in ENCRYPTED_SENSITIVITIES


def encrypt_body(plaintext: str, domain: str, keys_dir: Path) -> str:
    """
    Encrypt a document body using the vault's age public key.

    Args:
        plaintext: Document body content (markdown)
        domain: Vault domain name
        keys_dir: Base keys directory

    Returns:
        Age-encrypted ciphertext (binary, to be stored as-is)
    """
    recipient = _load_recipient(keys_dir, domain)
    encrypted = pyrage.encrypt(plaintext.encode("utf-8"), [recipient])
    # Return as latin-1 string so it can be stored in the markdown body
    # The SLOS runtime writes it as bytes in the document
    return encrypted.decode("latin-1")


def decrypt_body(ciphertext: str, domain: str, keys_dir: Path) -> str:
    """
    Decrypt a document body using the vault's age secret key.

    Args:
        ciphertext: Age-encrypted content
        domain: Vault domain name
        keys_dir: Base keys directory

    Returns:
        Decrypted plaintext (utf-8 string)
    """
    identity = _load_identity(keys_dir, domain)
    # Convert back from latin-1 string to bytes for decryption
    decrypted = pyrage.decrypt(ciphertext.encode("latin-1"), [identity])
    return decrypted.decode("utf-8")


def encrypt_document(content: str, domain: str, sensitivity: str, keys_dir: Path) -> tuple[str, bool]:
    """
    Conditionally encrypt a full document body.

    Args:
        content: Document body (after frontmatter)
        domain: Vault domain name
        sensitivity: Document sensitivity level
        keys_dir: Base keys directory

    Returns:
        Tuple of (content, was_encrypted). Content is encrypted if sensitivity warrants it.
    """
    if not should_encrypt(domain, sensitivity):
        return content, False

    try:
        encrypted = encrypt_body(content, domain, keys_dir)
        return encrypted, True
    except FileNotFoundError:
        logger.warning(
            f"No encryption key for vault '{domain}' — writing plaintext. "
            f"Generate keys with: carryall vault generate-encryption-key {domain}"
        )
        return content, False


def decrypt_document(content: str, domain: str, keys_dir: Path) -> str:
    """
    Conditionally decrypt a document body. Returns plaintext as-is.

    Args:
        content: Document content (may or may not be encrypted)
        domain: Vault domain name
        keys_dir: Base keys directory

    Returns:
        Decrypted content (or original if not encrypted)
    """
    if not is_encrypted(content):
        return content

    try:
        return decrypt_body(content, domain, keys_dir)
    except FileNotFoundError:
        logger.error(f"Cannot decrypt document from vault '{domain}' — missing key")
        raise
    except Exception as e:
        logger.error(f"Decryption failed for vault '{domain}': {e}")
        raise
