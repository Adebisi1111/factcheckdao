"""Shared helpers for FactCheckDAO direct-mode tests."""

from typing import Any


def to_hex(addr: Any) -> str:
    """Return hex for an address-like value."""
    if hasattr(addr, "as_hex"):
        return addr.as_hex
    if isinstance(addr, bytes):
        try:
            from genlayer.py.types import Address
            return str(Address(addr))
        except Exception:
            return "0x" + addr.hex()
    s = str(addr)
    if s.startswith("0x") or s.startswith("0X"):
        return s
    return "0x" + s
