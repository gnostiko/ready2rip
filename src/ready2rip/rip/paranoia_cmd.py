# SPDX-License-Identifier: GPL-3.0-or-later
"""Build cdparanoia / libcdio-paranoia argv with backend-aware flags."""

from __future__ import annotations

from ready2rip.disc.overread import OverreadCapability
from ready2rip.rip.secure import paranoia_flags_for_mode
from ready2rip.util import paranoia_backend


def absolute_span_arg(start_sector: int, end_sector: int) -> str:
    """Format ``[start, end)`` for libcdio/Xiph span syntax.

    libcdio-paranoia parses ``[.A]-[.B]`` as start sector *A* and a *relative*
    end offset *B* (last inclusive sector = A+B).
    """
    start = int(start_sector)
    end = int(end_sector)
    if end <= start:
        raise ValueError(f'Invalid sector span [{start}, {end})')
    rel_end = end - start - 1
    return f'[.{start}]-[.{rel_end}]'


def want_force_overread(
    sample_offset: int,
    overread: OverreadCapability | None,
) -> bool:
    """True when libcdio should pass ``--force-overread`` for this offset."""
    off = int(sample_offset)
    if off == 0 or overread is None:
        return False
    if off > 0:
        return bool(overread.lead_out)
    return bool(overread.lead_in)


def build_paranoia_argv(
    binary: str,
    device: str,
    *,
    mode: str,
    sample_offset: int = 0,
    force_overread: bool = False,
    quiet: bool = False,
) -> list[str]:
    """Shared argv prefix: binary, WAV out, device, offset, mode flags.

    Caller appends the span/track argument and output path.
    """
    cmd = [str(binary), '-w', '-d', str(device)]
    off = int(sample_offset)
    if off:
        cmd.extend(['-O', str(off)])
    if force_overread and paranoia_backend(binary) == 'libcdio':
        cmd.append('--force-overread')
    cmd.extend(paranoia_flags_for_mode(mode))
    if quiet:
        cmd.append('-q')
    return cmd
