"""CPU topology: which logical processors share an L3 cache (a CCD on AMD Ryzen).

Windows tends to pack a few busy single-threaded processes onto one CCD. On CPUs with
more than one CCD (for example the Ryzen 9 9950X3D) that makes one CCD run much hotter
than the other, while spreading the same work over both keeps the peak temperature lower.
`spread_masks` returns one affinity mask per L3 group so work can be assigned round-robin.

Only Windows is supported; elsewhere every function reports a single group and changes
nothing.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

_RELATION_CACHE = 2
_PROCESS_SET_INFORMATION = 0x0200
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass(frozen=True, slots=True)
class CacheGroup:
    """Logical processors (as a bit mask, processor group 0) sharing one L3 cache."""

    mask: int
    l3_bytes: int

    @property
    def processors(self) -> tuple[int, ...]:
        return tuple(bit for bit in range(self.mask.bit_length()) if self.mask >> bit & 1)


def l3_groups() -> list[CacheGroup]:
    """L3 cache groups in processor order; empty when unknown or not on Windows."""
    if sys.platform != "win32":
        return []
    try:
        return _windows_l3_groups()
    except OSError:
        return []


def spread_masks() -> list[int]:
    """One affinity mask per L3 group, or an empty list when there is only one group."""
    groups = l3_groups()
    return [group.mask for group in groups] if len(groups) > 1 else []


def set_affinity(pid: int, mask: int) -> bool:
    """Restrict a process to the processors in `mask` (Windows only)."""
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(
        _PROCESS_SET_INFORMATION | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        return False
    try:
        return bool(kernel32.SetProcessAffinityMask(handle, ctypes.c_size_t(mask)))
    finally:
        kernel32.CloseHandle(handle)


def affinity(pid: int) -> int | None:
    """The affinity mask of a process, or None when it cannot be read."""
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        process_mask = ctypes.c_size_t()
        system_mask = ctypes.c_size_t()
        if not kernel32.GetProcessAffinityMask(
            handle, ctypes.byref(process_mask), ctypes.byref(system_mask)
        ):
            return None
        return int(process_mask.value)
    finally:
        kernel32.CloseHandle(handle)


class _GroupAffinity(ctypes.Structure):
    _fields_ = [
        ("Mask", ctypes.c_size_t),
        ("Group", ctypes.c_ushort),
        ("Reserved", ctypes.c_ushort * 3),
    ]


class _CacheRelationship(ctypes.Structure):
    _fields_ = [
        ("Level", ctypes.c_ubyte),
        ("Associativity", ctypes.c_ubyte),
        ("LineSize", ctypes.c_ushort),
        ("CacheSize", ctypes.c_ulong),
        ("Type", ctypes.c_int),
        ("Reserved", ctypes.c_ubyte * 18),
        ("GroupCount", ctypes.c_ushort),
        ("GroupMask", _GroupAffinity),
    ]


def _windows_l3_groups() -> list[CacheGroup]:
    if sys.platform != "win32":
        return []
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    size = ctypes.c_ulong(0)
    kernel32.GetLogicalProcessorInformationEx(_RELATION_CACHE, None, ctypes.byref(size))
    if size.value == 0:
        return []
    buffer = ctypes.create_string_buffer(size.value)
    if not kernel32.GetLogicalProcessorInformationEx(
        _RELATION_CACHE, buffer, ctypes.byref(size)
    ):
        raise OSError(ctypes.get_last_error())
    groups: list[CacheGroup] = []
    offset = 0
    while offset < size.value:
        header = (ctypes.c_ulong * 2).from_buffer_copy(buffer, offset)
        relationship, entry_size = int(header[0]), int(header[1])
        if entry_size == 0:
            break
        if relationship == _RELATION_CACHE:
            cache = _CacheRelationship.from_buffer_copy(buffer, offset + 8)
            if cache.Level == 3 and cache.GroupMask.Group == 0:
                groups.append(CacheGroup(int(cache.GroupMask.Mask), int(cache.CacheSize)))
        offset += entry_size
    return sorted(groups, key=lambda group: group.mask & -group.mask)
