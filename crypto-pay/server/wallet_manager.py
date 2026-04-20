"""Wallet address validation for all supported cryptocurrencies."""

import re
import hashlib
import base64
from typing import Dict, Optional, Tuple

from logger_setup import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Base58 alphabet (Bitcoin)
# ---------------------------------------------------------------------------
_BASE58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_MAP = {char: idx for idx, char in enumerate(_BASE58_ALPHABET)}


def _b58decode(s: str) -> bytes:
    """Decode a Base58-encoded string to bytes.

    Args:
        s: Base58-encoded string.

    Returns:
        Decoded bytes.

    Raises:
        ValueError: If the input contains invalid characters.
    """
    num = 0
    for char in s.encode("ascii"):
        if char not in _BASE58_MAP:
            raise ValueError(f"Invalid Base58 character: {chr(char)!r}")
        num = num * 58 + _BASE58_MAP[char]

    pad = 0
    for char in s.encode("ascii"):
        if char == _BASE58_ALPHABET[0]:
            pad += 1
        else:
            break

    result = []
    while num > 0:
        num, remainder = divmod(num, 256)
        result.append(remainder)
    result.extend([0] * pad)
    return bytes(reversed(result))


def _b58check_decode(s: str) -> Tuple[int, bytes]:
    """Decode a Base58Check-encoded string and verify its checksum.

    Args:
        s: Base58Check-encoded string.

    Returns:
        Tuple of (version_byte, payload_bytes).

    Raises:
        ValueError: If the checksum does not match.
    """
    decoded = _b58decode(s)
    payload, checksum = decoded[:-4], decoded[-4:]
    computed = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if computed != checksum:
        raise ValueError("Base58Check checksum mismatch")
    return payload[0], payload[1:]


# ---------------------------------------------------------------------------
# Bech32 helpers (BIP-0173)
# ---------------------------------------------------------------------------
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_GENERATOR = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]


def _bech32_polymod(values) -> int:
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= _GENERATOR[i] if (b >> i) & 1 else 0
    return chk


def _bech32_hrp_expand(hrp: str):
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _bech32_verify_checksum(hrp: str, data) -> bool:
    return _bech32_polymod(_bech32_hrp_expand(hrp) + list(data)) == 1


def _bech32_decode(bech: str) -> Tuple[Optional[str], Optional[list]]:
    if any(ord(x) < 33 or ord(x) > 126 for x in bech):
        return None, None
    if bech.lower() != bech and bech.upper() != bech:
        return None, None
    bech = bech.lower()
    pos = bech.rfind("1")
    if pos < 1 or pos + 7 > len(bech) or len(bech) > 90:
        return None, None
    if not all(x in _BECH32_CHARSET for x in bech[pos + 1:]):
        return None, None
    hrp = bech[:pos]
    data = [_BECH32_CHARSET.index(x) for x in bech[pos + 1:]]
    if not _bech32_verify_checksum(hrp, data):
        return None, None
    return hrp, data[:-6]


def _is_valid_bech32(address: str, expected_hrp: str) -> bool:
    hrp, _ = _bech32_decode(address)
    return hrp == expected_hrp


# ---------------------------------------------------------------------------
# EIP-55 checksum helper
# ---------------------------------------------------------------------------

def _eth_checksum_address(address: str) -> str:
    """Return the EIP-55 checksummed Ethereum address using keccak-256.

    Implements keccak-256 in pure Python (without external dependencies)
    since Python's ``hashlib.sha3_256`` uses the NIST variant which differs
    from the Ethereum keccak variant.

    Args:
        address: 0x-prefixed Ethereum address (any case).

    Returns:
        EIP-55 checksummed address string.
    """
    addr = address.lower().lstrip("0x")
    hashed = _keccak256(addr.encode("ascii")).hex()

    checksummed = "0x" + "".join(
        c.upper() if int(hashed[i], 16) >= 8 else c
        for i, c in enumerate(addr)
    )
    return checksummed


def _keccak256(data: bytes) -> bytes:
    """Compute keccak-256 (Ethereum variant) of *data*.

    Pure-Python implementation that does not require any third-party library.

    Args:
        data: Input bytes.

    Returns:
        32-byte digest.
    """
    # Keccak-256 constants
    KECCAK_RHO = [
        1,  3,  6, 10, 15, 21, 28, 36, 45, 55,  2, 14,
        27, 41, 56,  8, 25, 43, 62, 18, 39, 61, 20, 44,
    ]
    KECCAK_PI = [
        10,  7, 11, 17, 18,  3,  5, 16,  8, 21, 24,  4,
        15, 23, 19, 13, 12,  2, 20, 14, 22,  9,  6,  1,
    ]
    KECCAK_RC = [
        0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
        0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
        0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
        0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
        0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
        0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
        0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
        0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
    ]

    MASK64 = (1 << 64) - 1

    def rot64(v: int, n: int) -> int:
        return ((v << n) | (v >> (64 - n))) & MASK64

    # Padding (Keccak, not SHA-3: 0x01 suffix)
    rate_bytes = 136  # 1088 bits for keccak-256
    msg = bytearray(data)
    msg.append(0x01)
    pad_len = rate_bytes - (len(msg) % rate_bytes)
    msg.extend(b"\x00" * (pad_len - 1))
    msg.append(0x80)

    # Absorb
    state = [0] * 25  # 5×5 64-bit lanes

    def absorb_block(block: bytes) -> None:
        for i in range(rate_bytes // 8):
            val = int.from_bytes(block[i * 8:(i + 1) * 8], "little")
            state[i] ^= val
        keccak_f(state)

    def keccak_f(s: list) -> None:
        for rc in KECCAK_RC:
            # θ
            c = [s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20] for x in range(5)]
            d = [c[(x - 1) % 5] ^ rot64(c[(x + 1) % 5], 1) for x in range(5)]
            for x in range(5):
                for y in range(5):
                    s[x + 5 * y] ^= d[x]
            # ρ + π
            last = s[1]
            for i in range(24):
                j = KECCAK_PI[i]
                tmp = s[j]
                s[j] = rot64(last, KECCAK_RHO[i])
                last = tmp
            # χ
            for y in range(5):
                row = s[5 * y: 5 * y + 5]
                for x in range(5):
                    s[x + 5 * y] = row[x] ^ ((~row[(x + 1) % 5]) & row[(x + 2) % 5])
                    s[x + 5 * y] &= MASK64
            # ι
            s[0] ^= rc
            s[0] &= MASK64

    for block_start in range(0, len(msg), rate_bytes):
        absorb_block(msg[block_start:block_start + rate_bytes])

    # Squeeze first 32 bytes
    return b"".join(state[i].to_bytes(8, "little") for i in range(4))


# ---------------------------------------------------------------------------
# Per-coin validators
# ---------------------------------------------------------------------------

def validate_bitcoin(address: str) -> bool:
    """Validate a Bitcoin address (Base58Check legacy or Bech32 native).

    Args:
        address: Bitcoin address string.

    Returns:
        True if the address is valid.
    """
    address = address.strip()
    if address.startswith(("bc1q", "bc1p")):
        return _is_valid_bech32(address, "bc")

    if len(address) < 25 or len(address) > 34:
        return False
    try:
        version, _ = _b58check_decode(address)
        return version in (0x00, 0x05)
    except (ValueError, Exception):
        return False


def validate_monero(address: str) -> bool:
    """Validate a Monero address (standard 95-char or subaddress 106-char).

    Args:
        address: Monero address string.

    Returns:
        True if the address is valid.
    """
    address = address.strip()
    return (
        len(address) in (95, 106)
        and address[0] in ("4", "8")
        and re.fullmatch(r"[123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz]+", address)
        is not None
    )


def validate_ethereum(address: str) -> bool:
    """Validate an Ethereum/ERC-20 address (0x + 40 hex, optional EIP-55).

    Args:
        address: Ethereum address string.

    Returns:
        True if the address is valid.
    """
    address = address.strip()
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
        return False
    # If mixed-case, enforce EIP-55 checksum
    if address != address.lower() and address != address.upper():
        return address == _eth_checksum_address(address)
    return True


def validate_litecoin(address: str) -> bool:
    """Validate a Litecoin address (L/M/3 prefix or ltc1 Bech32).

    Args:
        address: Litecoin address string.

    Returns:
        True if the address is valid.
    """
    address = address.strip()
    if address.startswith("ltc1"):
        return _is_valid_bech32(address, "ltc")
    if not (25 <= len(address) <= 34):
        return False
    try:
        version, _ = _b58check_decode(address)
        return version in (0x30, 0x32, 0x05)
    except (ValueError, Exception):
        return False


def validate_trc20(address: str) -> bool:
    """Validate a TRON / TRC-20 address (T + 33 Base58 chars).

    Args:
        address: TRON address string.

    Returns:
        True if the address is valid.
    """
    address = address.strip()
    if not address.startswith("T") or len(address) != 34:
        return False
    try:
        decoded = _b58decode(address)
        return len(decoded) >= 20
    except (ValueError, Exception):
        return False


def validate_bitcoin_cash(address: str) -> bool:
    """Validate a Bitcoin Cash address (cashaddr or legacy).

    Args:
        address: BCH address string.

    Returns:
        True if the address is valid.
    """
    address = address.strip()
    if address.startswith("bitcoincash:"):
        payload = address[len("bitcoincash:"):]
        return bool(re.fullmatch(r"[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{42}", payload.lower()))
    # Legacy P2PKH / P2SH
    if 25 <= len(address) <= 34:
        try:
            version, _ = _b58check_decode(address)
            return version in (0x00, 0x05)
        except (ValueError, Exception):
            return False
    return False


def validate_solana(address: str) -> bool:
    """Validate a Solana address (32–44 Base58 chars, decodes to 32 bytes).

    Args:
        address: Solana address string.

    Returns:
        True if the address is valid.
    """
    address = address.strip()
    if not 32 <= len(address) <= 44:
        return False
    try:
        decoded = _b58decode(address)
        return len(decoded) == 32
    except (ValueError, Exception):
        return False


def validate_dogecoin(address: str) -> bool:
    """Validate a Dogecoin address (D prefix, 34 chars, Base58Check).

    Args:
        address: Dogecoin address string.

    Returns:
        True if the address is valid.
    """
    address = address.strip()
    if not address.startswith("D") or len(address) != 34:
        return False
    try:
        version, _ = _b58check_decode(address)
        return version == 0x1E
    except (ValueError, Exception):
        return False


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_VALIDATORS: Dict[str, object] = {
    "BTC": validate_bitcoin,
    "XMR": validate_monero,
    "ETH": validate_ethereum,
    "USDC": validate_ethereum,
    "USDT_ERC20": validate_ethereum,
    "USDT_TRC20": validate_trc20,
    "LTC": validate_litecoin,
    "BCH": validate_bitcoin_cash,
    "SOL": validate_solana,
    "DOGE": validate_dogecoin,
}


class WalletManager:
    """Validates and manages cryptocurrency wallet addresses."""

    def __init__(self, db) -> None:
        """Initialise the wallet manager.

        Args:
            db: Initialised :class:`database.Database` instance.
        """
        self._db = db

    def validate(self, coin: str, address: str) -> bool:
        """Validate an address for the given coin.

        Args:
            coin: Coin ticker (e.g. ``"BTC"``).
            address: Wallet address string.

        Returns:
            True if the address passes validation.
        """
        validator = _VALIDATORS.get(coin.upper())
        if not validator:
            log.warning(f"No validator for coin: {coin}")
            return False
        try:
            result = validator(address)  # type: ignore[operator]
            if not result:
                log.debug(f"Address validation failed for {coin}: {address!r}")
            return result
        except Exception as exc:
            log.error(f"Unexpected error validating {coin} address: {exc}")
            return False

    def get_address(self, coin: str) -> Optional[str]:
        """Return the active wallet address for the given coin.

        Args:
            coin: Coin ticker.

        Returns:
            Address string, or ``None``.
        """
        return self._db.get_address(coin.upper())

    def add_address(self, coin: str, address: str, label: str = "") -> bool:
        """Validate and add a new wallet address.

        Args:
            coin: Coin ticker.
            address: Public wallet address.
            label: Optional human-readable label.

        Returns:
            True if the address was accepted and stored.
        """
        if not self.validate(coin, address):
            log.error(f"Refused to store invalid {coin} address: {address!r}")
            return False
        self._db.add_address(coin.upper(), address, label)
        log.info(f"Added {coin} address: {address}")
        return True

    def validate_all(self) -> Dict[str, bool]:
        """Validate every address currently stored in the database.

        Returns:
            Mapping of ``coin:address`` → validation result.
        """
        results: Dict[str, bool] = {}
        for row in self._db.list_addresses():
            coin = row["coin"]
            addr = row["address"]
            key = f"{coin}:{addr}"
            results[key] = self.validate(coin, addr)
        return results
