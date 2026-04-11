"""
Agent Key Store - Secure Ed25519 key management for SLOS authentication.

Keys are stored as 32-byte seeds in files with 0o600 permissions.
"""

import os
import base64
from pathlib import Path

import nacl.signing
import nacl.encoding


class AgentKeyStore:
    """
    Secure storage for agent Ed25519 signing keys.

    Keys are stored in a directory with restricted permissions.
    Each key is a 32-byte seed file named {agent_id}.key.

    Example:
        ```python
        store = AgentKeyStore("~/.carryall/keys")

        # Generate new keypair
        public_key, secret_path = store.generate_keypair("finance-agent")
        print(f"Add to SLOS config/agents.yaml:")
        print(f"  finance-agent: {public_key}")

        # Load key for signing
        signing_key = store.load_signing_key("finance-agent")
        signature = signing_key.sign(message)
        ```
    """

    def __init__(self, keys_dir: str = "~/.carryall/keys"):
        """
        Initialize key store.

        Args:
            keys_dir: Directory for key files (will be created with 0o700)
        """
        self.keys_dir = Path(keys_dir).expanduser()
        self._ensure_keys_dir()
        self._signing_keys: dict[str, nacl.signing.SigningKey] = {}

    def _ensure_keys_dir(self):
        """Create keys directory with secure permissions."""
        if not self.keys_dir.exists():
            self.keys_dir.mkdir(parents=True, mode=0o700)
        else:
            # Ensure permissions are correct
            os.chmod(self.keys_dir, 0o700)

    def generate_keypair(self, agent_id: str, overwrite: bool = False) -> tuple[str, str]:
        """
        Generate and store new Ed25519 keypair.

        Args:
            agent_id: Agent identifier (alphanumeric + hyphens)
            overwrite: If True, replace existing key

        Returns:
            Tuple of (public_key_base64, secret_path)

        Raises:
            FileExistsError: If key exists and overwrite=False
        """
        secret_path = self.keys_dir / f"{agent_id}.key"

        if secret_path.exists() and not overwrite:
            raise FileExistsError(
                f"Key already exists for {agent_id} at {secret_path}. "
                f"Use overwrite=True to replace."
            )

        # Generate Ed25519 signing key
        signing_key = nacl.signing.SigningKey.generate()

        # Save secret key (32-byte seed)
        with open(secret_path, "wb") as f:
            f.write(bytes(signing_key))
        os.chmod(secret_path, 0o600)

        # Return public key in base64 (for SLOS config)
        public_key_b64 = base64.b64encode(bytes(signing_key.verify_key)).decode()

        return public_key_b64, str(secret_path)

    def import_key(self, agent_id: str, secret_key_bytes: bytes, overwrite: bool = False):
        """
        Import existing secret key.

        Args:
            agent_id: Agent identifier
            secret_key_bytes: 32-byte Ed25519 seed
            overwrite: If True, replace existing key

        Raises:
            ValueError: If key is not 32 bytes
            FileExistsError: If key exists and overwrite=False
        """
        if len(secret_key_bytes) != 32:
            raise ValueError(f"Secret key must be 32 bytes, got {len(secret_key_bytes)}")

        secret_path = self.keys_dir / f"{agent_id}.key"

        if secret_path.exists() and not overwrite:
            raise FileExistsError(f"Key already exists for {agent_id}")

        with open(secret_path, "wb") as f:
            f.write(secret_key_bytes)
        os.chmod(secret_path, 0o600)

    def import_key_base64(self, agent_id: str, secret_key_b64: str, overwrite: bool = False):
        """
        Import secret key from base64 string.

        Args:
            agent_id: Agent identifier
            secret_key_b64: Base64-encoded 32-byte seed
            overwrite: If True, replace existing key
        """
        secret_key_bytes = base64.b64decode(secret_key_b64)
        self.import_key(agent_id, secret_key_bytes, overwrite)

    def load_signing_key(self, agent_id: str) -> nacl.signing.SigningKey:
        """
        Load agent's signing key.

        Args:
            agent_id: Agent identifier

        Returns:
            PyNaCl SigningKey object

        Raises:
            FileNotFoundError: If key doesn't exist
        """
        if agent_id in self._signing_keys:
            return self._signing_keys[agent_id]

        key_path = self.keys_dir / f"{agent_id}.key"

        if not key_path.exists():
            raise FileNotFoundError(
                f"No key found for agent '{agent_id}'. "
                f"Run: carryall keys generate {agent_id}"
            )

        with open(key_path, "rb") as f:
            seed = f.read()

        signing_key = nacl.signing.SigningKey(seed)
        self._signing_keys[agent_id] = signing_key

        return signing_key

    def get_public_key(self, agent_id: str) -> str:
        """
        Get agent's public key in hex format (for signature verification).

        Args:
            agent_id: Agent identifier

        Returns:
            Hex-encoded public key (for envelope signature verification)
        """
        signing_key = self.load_signing_key(agent_id)
        return bytes(signing_key.verify_key).hex()

    def get_public_key_base64(self, agent_id: str) -> str:
        """
        Get agent's public key in base64 format (for SLOS config).

        Args:
            agent_id: Agent identifier

        Returns:
            Base64-encoded public key (for SLOS config/agents.yaml)
        """
        signing_key = self.load_signing_key(agent_id)
        return base64.b64encode(bytes(signing_key.verify_key)).decode()

    def has_key(self, agent_id: str) -> bool:
        """Check if key exists for agent."""
        return (self.keys_dir / f"{agent_id}.key").exists()

    def list_agents(self) -> list[str]:
        """List all agents with stored keys."""
        return sorted([p.stem for p in self.keys_dir.glob("*.key")])

    def delete_key(self, agent_id: str) -> bool:
        """
        Delete agent's key.

        Args:
            agent_id: Agent identifier

        Returns:
            True if key was deleted, False if it didn't exist
        """
        key_path = self.keys_dir / f"{agent_id}.key"

        if key_path.exists():
            # Clear from cache
            self._signing_keys.pop(agent_id, None)
            key_path.unlink()
            return True
        return False
