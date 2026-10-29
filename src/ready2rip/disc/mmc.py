# SPDX-License-Identifier: GPL-3.0-or-later
"""Linux CD-ROM ioctl / SCSI READ CD helpers (TOC, Q-index, raw sectors)."""

from __future__ import annotations

import ctypes
import fcntl
import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

# linux/cdrom.h
_CDROMREADTOCHDR = 0x5305
_CDROMREADTOCENTRY = 0x5306
_CDROMMULTISESSION = 0x5310
_CDROM_LBA = 0x01
_CDROM_LEADOUT = 0xAA
_CDROM_DATA_TRACK = 0x04

# linux/scsi/sg.h
_SG_IO = 0x2285
_SG_DXFER_FROM_DEV = -3
_SG_DXFER_NONE = -1

_SCSI_READ_CD = 0xBE
_SCSI_SEEK10 = 0x2B
_DATA_RETRIES = 8
_SCSI_TIMEOUT_MS = 20_000
_MAX_SECTORS_PER_READ = 32


class _TocHeader(ctypes.Structure):
    _fields_ = [('cdth_trk0', ctypes.c_ubyte), ('cdth_trk1', ctypes.c_ubyte)]


class _Msf(ctypes.Structure):
    _fields_ = [
        ('minute', ctypes.c_ubyte),
        ('second', ctypes.c_ubyte),
        ('frame', ctypes.c_ubyte),
    ]


class _Addr(ctypes.Union):
    _fields_ = [('msf', _Msf), ('lba', ctypes.c_int)]


class _TocEntry(ctypes.Structure):
    _fields_ = [
        ('cdte_track', ctypes.c_ubyte),
        ('cdte_adr', ctypes.c_ubyte, 4),
        ('cdte_ctrl', ctypes.c_ubyte, 4),
        ('cdte_format', ctypes.c_ubyte),
        ('cdte_addr', _Addr),
        ('cdte_datamode', ctypes.c_ubyte),
    ]


class _Multisession(ctypes.Structure):
    _fields_ = [
        ('addr', _Addr),
        ('xa_flag', ctypes.c_ubyte),
        ('addr_format', ctypes.c_ubyte),
    ]


class _SgIoHdr(ctypes.Structure):
    _fields_ = [
        ('interface_id', ctypes.c_int),
        ('dxfer_direction', ctypes.c_int),
        ('cmd_len', ctypes.c_ubyte),
        ('mx_sb_len', ctypes.c_ubyte),
        ('iovec_count', ctypes.c_ushort),
        ('dxfer_len', ctypes.c_uint),
        ('dxferp', ctypes.c_void_p),
        ('cmdp', ctypes.c_void_p),
        ('sbp', ctypes.c_void_p),
        ('timeout', ctypes.c_uint),
        ('flags', ctypes.c_uint),
        ('pack_id', ctypes.c_int),
        ('usr_ptr', ctypes.c_void_p),
        ('status', ctypes.c_ubyte),
        ('masked_status', ctypes.c_ubyte),
        ('msg_status', ctypes.c_ubyte),
        ('sb_len_wr', ctypes.c_ubyte),
        ('host_status', ctypes.c_ushort),
        ('driver_status', ctypes.c_ushort),
        ('resid', ctypes.c_int),
        ('duration', ctypes.c_uint),
        ('info', ctypes.c_uint),
    ]


@dataclass(frozen=True)
class RawTocTrack:
    """One TOC entry from CDROMREADTOCENTRY (LBA, 0 = MSF 00:02:00)."""

    number: int
    start_lba: int
    is_data: bool
    datamode: int  # 1 / 2 / 0 unknown


@dataclass
class RawToc:
    tracks: list[RawTocTrack]
    leadout_lba: int
    last_session_lba: int | None = None
    xa: bool = False

    @property
    def audio_tracks(self) -> list[RawTocTrack]:
        return [t for t in self.tracks if not t.is_data]

    @property
    def data_tracks(self) -> list[RawTocTrack]:
        return [t for t in self.tracks if t.is_data]


class MmcDevice:
    """Open optical device for ioctl / SG_IO; reuse the fd across commands."""

    def __init__(self, device: str, *, nonblock: bool = True) -> None:
        flags = os.O_RDONLY
        if nonblock:
            flags |= os.O_NONBLOCK
        self.device = device
        self.fd = os.open(device, flags)

    def close(self) -> None:
        fd = getattr(self, 'fd', -1)
        if fd >= 0:
            os.close(fd)
            self.fd = -1

    def __enter__(self) -> MmcDevice:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def read_q_index(self, lba: int) -> int | None:
        buf = bytearray(16)
        cdb = _read_cd_cdb(lba, sectors=1, main_channel=0x00, subchannel=0x02)
        data = self._sg_read(cdb, buf)
        if data is None or len(data) < 3:
            return None
        return int(data[2])

    def read_raw_sectors(self, lba: int, count: int) -> bytes | None:
        n = max(1, min(int(count), _MAX_SECTORS_PER_READ))
        buf = bytearray(2352 * n)
        cdb = _read_cd_cdb(lba, sectors=n, main_channel=0xF8, subchannel=0x00)
        data = self._sg_read(cdb, buf)
        if data is None:
            return None
        expect = 2352 * n
        if len(data) < expect:
            return None
        return bytes(data[:expect])

    def read_raw_sector(self, lba: int, *, retries: int = _DATA_RETRIES) -> bytes | None:
        last: bytes | None = None
        for _attempt in range(max(1, retries)):
            data = self.read_raw_sectors(lba, 1)
            if data is not None and len(data) >= 2352:
                last = data[:2352]
                if _mode1_edc_ok(last) or not _looks_mode1(last):
                    return last
        return last

    def seek_lba(self, lba: int) -> bool:
        """SCSI SEEK(10) — force the head off the current cached region."""
        cdb = bytes(
            [
                _SCSI_SEEK10,
                0x00,
                (lba >> 24) & 0xFF,
                (lba >> 16) & 0xFF,
                (lba >> 8) & 0xFF,
                lba & 0xFF,
                0x00,
                0x00,
                0x00,
                0x00,
            ]
        )
        return self._sg_nodata(cdb)

    def _sg_nodata(self, cdb: bytes) -> bool:
        sense = bytearray(32)
        cdb_buf = (ctypes.c_ubyte * len(cdb)).from_buffer_copy(cdb)
        sense_buf = (ctypes.c_ubyte * len(sense)).from_buffer(sense)
        hdr = _SgIoHdr()
        hdr.interface_id = ord('S')
        hdr.dxfer_direction = _SG_DXFER_NONE
        hdr.cmd_len = len(cdb)
        hdr.mx_sb_len = len(sense)
        hdr.dxfer_len = 0
        hdr.cmdp = ctypes.cast(cdb_buf, ctypes.c_void_p)
        hdr.sbp = ctypes.cast(sense_buf, ctypes.c_void_p)
        hdr.timeout = _SCSI_TIMEOUT_MS
        try:
            fcntl.ioctl(self.fd, _SG_IO, hdr)
        except OSError as exc:
            log.debug('SG_IO SEEK failed on %s: %s', self.device, exc)
            return False
        return hdr.status in (0,)

    def _sg_read(self, cdb: bytes, buf: bytearray) -> bytes | None:
        sense = bytearray(32)
        cdb_buf = (ctypes.c_ubyte * len(cdb)).from_buffer_copy(cdb)
        data_buf = (ctypes.c_ubyte * len(buf)).from_buffer(buf)
        sense_buf = (ctypes.c_ubyte * len(sense)).from_buffer(sense)
        hdr = _SgIoHdr()
        hdr.interface_id = ord('S')
        hdr.dxfer_direction = _SG_DXFER_FROM_DEV
        hdr.cmd_len = len(cdb)
        hdr.mx_sb_len = len(sense)
        hdr.dxfer_len = len(buf)
        hdr.dxferp = ctypes.cast(data_buf, ctypes.c_void_p)
        hdr.cmdp = ctypes.cast(cdb_buf, ctypes.c_void_p)
        hdr.sbp = ctypes.cast(sense_buf, ctypes.c_void_p)
        hdr.timeout = _SCSI_TIMEOUT_MS
        try:
            fcntl.ioctl(self.fd, _SG_IO, hdr)
        except OSError as exc:
            log.debug('SG_IO failed on %s: %s', self.device, exc)
            return None
        if hdr.status not in (0,):
            return None
        got = len(buf) - max(0, int(hdr.resid))
        if got <= 0:
            return None
        return bytes(buf[:got])


def read_raw_toc(device: str) -> RawToc | None:
    """Return the full TOC (audio + data) or None if ioctl is unavailable."""
    try:
        with MmcDevice(device) as mmc:
            hdr = _TocHeader()
            fcntl.ioctl(mmc.fd, _CDROMREADTOCHDR, hdr)
            first, last = int(hdr.cdth_trk0), int(hdr.cdth_trk1)
            if first < 1 or last < first or last > 99:
                return None
            tracks: list[RawTocTrack] = []
            for num in range(first, last + 1):
                entry = _read_toc_entry(mmc.fd, num)
                if entry is None:
                    continue
                tracks.append(entry)
            lead = _read_toc_entry(mmc.fd, _CDROM_LEADOUT)
            leadout = lead.start_lba if lead is not None else 0
            session_lba, xa = _read_multisession(mmc.fd)
            if not tracks or leadout <= 0:
                return None
            return RawToc(
                tracks=tracks,
                leadout_lba=leadout,
                last_session_lba=session_lba,
                xa=xa,
            )
    except OSError as exc:
        log.debug('TOC ioctl on %s failed: %s', device, exc)
        return None


def read_q_index(device: str, lba: int) -> int | None:
    """Return Q-subchannel INDEX (0/1/…) at *lba*, or None on failure."""
    try:
        with MmcDevice(device) as mmc:
            return mmc.read_q_index(lba)
    except OSError as exc:
        log.debug('Q-index open %s failed: %s', device, exc)
        return None


def read_raw_sector(device: str, lba: int, *, retries: int = _DATA_RETRIES) -> bytes | None:
    """Read one 2352-byte raw sector (sync+header+user+EDC/ECC)."""
    try:
        with MmcDevice(device, nonblock=False) as mmc:
            return mmc.read_raw_sector(lba, retries=retries)
    except OSError as exc:
        log.debug('Raw sector open %s failed: %s', device, exc)
        return None


def detect_sector_mode(sector: bytes) -> int:
    """Mode byte from a 2352-byte yellow-book sector (1 or 2)."""
    if len(sector) >= 16 and sector[15] in (1, 2):
        return int(sector[15])
    return 1


def _read_cd_cdb(
    lba: int,
    *,
    sectors: int,
    main_channel: int,
    subchannel: int,
) -> bytes:
    n = max(1, int(sectors))
    return bytes(
        [
            _SCSI_READ_CD,
            0x00,  # expected sector type: any
            (lba >> 24) & 0xFF,
            (lba >> 16) & 0xFF,
            (lba >> 8) & 0xFF,
            lba & 0xFF,
            (n >> 16) & 0xFF,
            (n >> 8) & 0xFF,
            n & 0xFF,
            main_channel & 0xFF,
            subchannel & 0xFF,
            0x00,
        ]
    )


def _read_toc_entry(fd: int, track: int) -> RawTocTrack | None:
    entry = _TocEntry()
    entry.cdte_track = track
    entry.cdte_format = _CDROM_LBA
    try:
        fcntl.ioctl(fd, _CDROMREADTOCENTRY, entry)
    except OSError:
        return None
    lba = int(entry.cdte_addr.lba)
    ctrl = int(entry.cdte_ctrl)
    is_data = bool(ctrl & _CDROM_DATA_TRACK)
    mode = int(entry.cdte_datamode)
    number = 0xAA if track == _CDROM_LEADOUT else track
    return RawTocTrack(
        number=number,
        start_lba=lba,
        is_data=is_data,
        datamode=mode,
    )


def _read_multisession(fd: int) -> tuple[int | None, bool]:
    ms = _Multisession()
    ms.addr_format = _CDROM_LBA
    try:
        fcntl.ioctl(fd, _CDROMMULTISESSION, ms)
    except OSError:
        return None, False
    lba = int(ms.addr.lba)
    xa = bool(ms.xa_flag)
    return (lba if lba > 0 else None), xa


def _looks_mode1(sector: bytes) -> bool:
    if len(sector) < 16:
        return False
    if sector[0] != 0x00 or sector[11] != 0x00:
        return False
    if sector[1:11] != b'\xff' * 10:
        return False
    return sector[15] == 1


def _mode1_edc_ok(sector: bytes) -> bool:
    """True when Mode 1 EDC matches (unknown layouts return True)."""
    if not _looks_mode1(sector) or len(sector) < 2068:
        return True
    expected = int.from_bytes(sector[2064:2068], 'little')
    return _crc32_edc(sector[:2064]) == expected


def _crc32_edc(data: bytes) -> int:
    """Mode-1 EDC polynomial 0xD8018001, init 0."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xD8018001
            else:
                crc >>= 1
    return crc & 0xFFFFFFFF
