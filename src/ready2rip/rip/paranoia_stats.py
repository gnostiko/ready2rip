# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse cdparanoia ``-e`` progress and summarise error-correction activity.

libcdio-paranoia / cdparanoia emit lines like::

    ##: 0 [read] @ 19992
    ##: 2 [fixup_edge] @ 44100
    ##: 6 [skip] @ 88200
    ##: 15 [finished] @ …

Stage names match ``paranoia_cb_mode_t`` in the paranoia API. Sample positions
are PCM frames (stereo sample pairs); CDDA has 588 frames per sector.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# CDDA: 44100 Hz stereo, 16-bit → 2352 bytes/sector → 588 frames/sector
FRAMES_PER_SECTOR = 588

# ##: <cb_id> [<stage>] @ <frame>
_PROGRESS_RE = re.compile(
    r'##:\s*(\d+)\s*\[([a-z_]+)\]\s*@\s*(-?\d+)',
    re.IGNORECASE,
)

# Stages that mean real correction / trouble (EAC-style “errors”).
_ERROR_STAGES = frozenset(
    {
        'skip',
        'readerr',
        'scratch',
        'repair',
        'cacheerr',
        'backoff',
    }
)
# Soft fixups (jitter / dropped / duped samples) — still logged, mild quality hit.
_FIXUP_STAGES = frozenset(
    {
        'fixup_edge',
        'fixup_atom',
        'fixup_dropped',
        'fixup_duped',
        'drift',
        'overlap',
    }
)


@dataclass
class ParanoiaStats:
    """Aggregated paranoia activity for one extraction pass."""

    reads: int = 0
    verifies: int = 0
    fixup_edge: int = 0
    fixup_atom: int = 0
    scratch: int = 0
    repair: int = 0
    skips: int = 0
    drift: int = 0
    backoff: int = 0
    overlap: int = 0
    fixup_dropped: int = 0
    fixup_duped: int = 0
    readerrs: int = 0
    cacheerrs: int = 0
    wrote: int = 0
    finished: bool = False
    # Absolute PCM frame positions for notable trouble (skip / readerr / scratch).
    suspicious_frames: list[int] = field(default_factory=list)
    # All stage hits we did not map (forward compatibility).
    other: dict[str, int] = field(default_factory=dict)
    exit_code: int | None = None
    raw_excerpt: str = ''

    def merge(self, other: ParanoiaStats) -> ParanoiaStats:
        """Accumulate *other* into this instance (test+copy combined)."""
        for name in (
            'reads',
            'verifies',
            'fixup_edge',
            'fixup_atom',
            'scratch',
            'repair',
            'skips',
            'drift',
            'backoff',
            'overlap',
            'fixup_dropped',
            'fixup_duped',
            'readerrs',
            'cacheerrs',
            'wrote',
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.finished = self.finished or other.finished
        self.suspicious_frames.extend(other.suspicious_frames)
        for key, val in other.other.items():
            self.other[key] = self.other.get(key, 0) + val
        if other.exit_code is not None:
            self.exit_code = other.exit_code
        if other.raw_excerpt and not self.raw_excerpt:
            self.raw_excerpt = other.raw_excerpt
        return self

    @property
    def fixup_total(self) -> int:
        return (
            self.fixup_edge
            + self.fixup_atom
            + self.fixup_dropped
            + self.fixup_duped
        )

    @property
    def hard_errors(self) -> int:
        return self.skips + self.readerrs + self.scratch + self.cacheerrs

    @property
    def had_errors(self) -> bool:
        """True when EAC would say “There were errors” (skips / hard errors)."""
        return self.hard_errors > 0 or self.repair > 0

    @property
    def had_corrections(self) -> bool:
        return self.had_errors or self.fixup_total > 0 or self.drift > 0

    def quality_percent(self, length_sectors: int = 0) -> float:
        """Approximate EAC “Track quality” (100% = clean secure read).

        Weights hard skips/read errors heavily; jitter fixups lightly.
        """
        if self.hard_errors == 0 and self.fixup_total == 0 and self.drift == 0:
            return 100.0
        denom = max(1, int(length_sectors) or (self.wrote // max(1, FRAMES_PER_SECTOR)) or 1)
        weighted = (
            self.skips * 8.0
            + self.readerrs * 8.0
            + self.scratch * 4.0
            + self.repair * 3.0
            + self.cacheerrs * 4.0
            + self.fixup_edge * 0.15
            + self.fixup_atom * 0.15
            + self.fixup_dropped * 0.25
            + self.fixup_duped * 0.25
            + self.drift * 0.5
            + self.backoff * 0.5
        )
        q = 100.0 * (1.0 - min(1.0, weighted / float(denom)))
        # Cap below 100 if any correction ran, even tiny.
        if self.had_corrections and q > 99.9:
            q = 99.9
        return max(0.0, min(100.0, q))

    def suspicious_msf_labels(self, *, limit: int = 12) -> list[str]:
        """Unique MSF positions for suspicious frames (EAC-style)."""
        seen: set[str] = set()
        out: list[str] = []
        for frame in sorted(self.suspicious_frames):
            if frame < 0:
                continue
            sector = frame // FRAMES_PER_SECTOR
            label = _msf_from_sectors(sector)
            if label in seen:
                continue
            seen.add(label)
            out.append(label)
            if len(out) >= limit:
                break
        return out

    def summary_lines(self, *, length_sectors: int = 0) -> list[str]:
        """Human lines for the per-track log (error correction section)."""
        lines: list[str] = []
        q = self.quality_percent(length_sectors)
        lines.append(f'Track quality {q:.1f} %')

        # Correction / trouble counters only (not routine read/verify/write).
        parts: list[str] = []
        if self.fixup_edge:
            parts.append(f'edge fixups={self.fixup_edge}')
        if self.fixup_atom:
            parts.append(f'atom fixups={self.fixup_atom}')
        if self.fixup_dropped:
            parts.append(f'dropped fixups={self.fixup_dropped}')
        if self.fixup_duped:
            parts.append(f'duped fixups={self.fixup_duped}')
        if self.overlap:
            parts.append(f'overlap adjust={self.overlap}')
        if self.drift:
            parts.append(f'drift={self.drift}')
        if self.repair:
            parts.append(f'repair={self.repair}')
        if self.scratch:
            parts.append(f'scratch={self.scratch}')
        if self.skips:
            parts.append(f'skips={self.skips}')
        if self.readerrs:
            parts.append(f'read errors={self.readerrs}')
        if self.cacheerrs:
            parts.append(f'cache errors={self.cacheerrs}')
        if self.backoff:
            parts.append(f'backoff={self.backoff}')
        if parts:
            lines.append('Error correction     : ' + ', '.join(parts))
        else:
            lines.append('Error correction     : none required')
        # Optional activity footprint for forensics (EAC does not show this).
        if self.reads or self.verifies:
            lines.append(
                f'Read activity         : {self.reads} reads, '
                f'{self.verifies} verify passes'
            )

        for pos in self.suspicious_msf_labels():
            lines.append(f'Suspicious position  {pos}')

        if self.had_errors:
            lines.append('There were errors')
        elif self.had_corrections:
            lines.append('Minor jitter corrections applied (no skips)')
        return lines

    def short_status(self) -> str:
        if self.had_errors:
            return 'errors'
        if self.had_corrections:
            return 'corrected'
        return 'clean'


def parse_paranoia_stderr(text: str, *, exit_code: int | None = None) -> ParanoiaStats:
    """Parse ``cdparanoia -e`` / verbose progress from *text*."""
    stats = ParanoiaStats(exit_code=exit_code)
    if not text:
        return stats

    # Keep a short excerpt of non-progress diagnostic lines for failures.
    diag: list[str] = []
    for line in text.splitlines():
        m = _PROGRESS_RE.search(line)
        if not m:
            stripped = line.strip()
            if stripped and not stripped.startswith('(') and 'PROGRESS' not in stripped:
                if any(
                    k in stripped.lower()
                    for k in (
                        'error',
                        'skip',
                        'fail',
                        'beyond',
                        'abort',
                        'scsi',
                        'transport',
                    )
                ):
                    diag.append(stripped)
            continue

        stage = m.group(2).lower()
        try:
            frame = int(m.group(3))
        except ValueError:
            frame = -1

        if stage == 'read':
            stats.reads += 1
        elif stage == 'verify':
            stats.verifies += 1
        elif stage == 'fixup_edge':
            stats.fixup_edge += 1
        elif stage == 'fixup_atom':
            stats.fixup_atom += 1
        elif stage == 'scratch':
            stats.scratch += 1
            if frame >= 0:
                stats.suspicious_frames.append(frame)
        elif stage == 'repair':
            stats.repair += 1
            if frame >= 0:
                stats.suspicious_frames.append(frame)
        elif stage == 'skip':
            stats.skips += 1
            if frame >= 0:
                stats.suspicious_frames.append(frame)
        elif stage == 'drift':
            stats.drift += 1
        elif stage == 'backoff':
            stats.backoff += 1
        elif stage == 'overlap':
            stats.overlap += 1
        elif stage == 'fixup_dropped':
            stats.fixup_dropped += 1
        elif stage == 'fixup_duped':
            stats.fixup_duped += 1
        elif stage == 'readerr':
            stats.readerrs += 1
            if frame >= 0:
                stats.suspicious_frames.append(frame)
        elif stage == 'cacheerr':
            stats.cacheerrs += 1
            if frame >= 0:
                stats.suspicious_frames.append(frame)
        elif stage == 'wrote':
            stats.wrote += 1
        elif stage == 'finished':
            stats.finished = True
        else:
            stats.other[stage] = stats.other.get(stage, 0) + 1

    if diag:
        stats.raw_excerpt = ' | '.join(diag[:6])
    return stats


def _msf_from_sectors(sectors: int) -> str:
    if sectors < 0:
        sectors = 0
    frames = sectors % 75
    total_sec = sectors // 75
    minutes, seconds = divmod(total_sec, 60)
    return f'{minutes:02d}:{seconds:02d}.{frames:02d}'
