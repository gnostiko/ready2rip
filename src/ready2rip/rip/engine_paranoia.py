# SPDX-License-Identifier: GPL-3.0-or-later
"""ParanoiaMixin methods for :class:`~ready2rip.rip.engine.RipEngine`."""

from __future__ import annotations

from ready2rip.rip._engine_shared import *  # noqa: F403

# NOTE: This mixin is composed onto RipEngine. Method bodies expect the full
# RipEngine attribute/helper surface (imports resolved in engine.py).


class ParanoiaMixin:
    def _paranoia_cmd_base(
        self,
        binary: str,
        device: str,
        *,
        mode: str,
        sample_offset: int = 0,
        force_overread: bool = False,
    ) -> list[str]:
        """Build shared cdparanoia argv for secure, normal, or burst extract."""
        from ready2rip.rip.paranoia_cmd import build_paranoia_argv

        return build_paranoia_argv(
            binary,
            device,
            mode=mode,
            sample_offset=sample_offset,
            force_overread=force_overread,
        )

    def _run_cdparanoia(
        self,
        device: str,
        track_number: int,
        wav_path: Path,
        *,
        mode: str,
        timeout: int,
        allow_nonzero: bool = False,
        sample_offset: int = 0,
        overread: OverreadCapability | None = None,
        on_extract_progress: Callable[[float], None] | None = None,
        expected_frames: int = 0,
        expected_bytes: int | None = None,
    ) -> ParanoiaStats:
        from ready2rip.rip.paranoia_cmd import want_force_overread

        binary = find_cdparanoia()
        if not binary:
            raise RuntimeError('cdparanoia not found')
        cmd = self._paranoia_cmd_base(
            binary,
            device,
            mode=mode,
            sample_offset=sample_offset,
            force_overread=want_force_overread(sample_offset, overread),
        )
        cmd.extend([str(track_number), str(wav_path)])
        return self._run_paranoia(
            cmd,
            what=f'cdparanoia {mode} track {track_number}',
            timeout=timeout,
            allow_nonzero=allow_nonzero,
            on_extract_progress=on_extract_progress,
            expected_frames=expected_frames,
            wav_path=wav_path,
            expected_bytes=expected_bytes,
        )

    def _run_cdparanoia_span(
        self,
        device: str,
        start_sector: int,
        end_sector: int,
        wav_path: Path,
        *,
        mode: str,
        timeout: int,
        allow_nonzero: bool = False,
        sample_offset: int = 0,
        overread: OverreadCapability | None = None,
        on_extract_progress: Callable[[float], None] | None = None,
        expected_frames: int = 0,
        expected_bytes: int | None = None,
    ) -> ParanoiaStats:
        """Extract absolute sector range ``[start, end)`` to *wav_path*.

        libcdio-paranoia parses ``[.A]-[.B]`` as start sector *A* and a
        *relative* end offset *B* (last inclusive sector = A+B) — not absolute
        end sector B. Passing absolute B made Copy Image request past the
        lead-out (“Time/sector offset goes beyond end of disc”).
        """
        from ready2rip.rip.paranoia_cmd import absolute_span_arg, want_force_overread

        binary = find_cdparanoia()
        if not binary:
            raise RuntimeError('cdparanoia not found')
        try:
            span = absolute_span_arg(start_sector, end_sector)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        cmd = self._paranoia_cmd_base(
            binary,
            device,
            mode=mode,
            sample_offset=sample_offset,
            force_overread=want_force_overread(sample_offset, overread),
        )
        cmd.extend([span, str(wav_path)])
        return self._run_paranoia(
            cmd,
            what=f'cdparanoia {mode} span {span} (sectors [{start_sector}, {end_sector}))',
            timeout=timeout,
            allow_nonzero=allow_nonzero,
            on_extract_progress=on_extract_progress,
            expected_frames=expected_frames,
            wav_path=wav_path,
            expected_bytes=expected_bytes,
        )

    def _assert_wav_ok(
        self,
        wav_path: Path,
        track_number: int,
        *,
        expected_bytes: int | None,
        min_ratio: float,
        label: str = 'secure',
    ) -> None:
        if not wav_path.is_file():
            raise RuntimeError(f'{label} extraction produced no file for track {track_number}')
        size = wav_path.stat().st_size
        if size < 1000:
            raise RuntimeError(f'{label} extraction produced empty data for track {track_number}')
        if expected_bytes and expected_bytes > 0:
            minimum = wav_size_minimum(expected_bytes, min_ratio)
            if size < minimum:
                got_pct = (100.0 * size / expected_bytes) if expected_bytes else 0.0
                if float(min_ratio) >= 1.0:
                    need = 'exact TOC length'
                else:
                    need = f'≥{min_ratio:.1%} of TOC length'
                raise RuntimeError(
                    f'{label} extraction incomplete for track {track_number}: '
                    f'{size} bytes < {minimum} required '
                    f'(got {got_pct:.1f}% of {expected_bytes} CDDA bytes; need {need})'
                )

    def _run_paranoia(
        self,
        cmd: list[str],
        *,
        what: str,
        timeout: int | None = None,
        allow_nonzero: bool = False,
        on_extract_progress: Callable[[float], None] | None = None,
        expected_frames: int = 0,
        wav_path: Path | None = None,
        expected_bytes: int | None = None,
    ) -> ParanoiaStats:
        """Run cdparanoia, stream ``-e`` progress, return :class:`ParanoiaStats`."""

        def register(proc: subprocess.Popen) -> None:
            with self._proc_lock:
                self._procs.add(proc)

        def unregister(proc: subprocess.Popen) -> None:
            with self._proc_lock:
                self._procs.discard(proc)

        return run_paranoia_streaming(
            cmd,
            what=what,
            timeout=timeout,
            allow_nonzero=allow_nonzero,
            on_extract_progress=on_extract_progress,
            expected_frames=expected_frames,
            wav_path=wav_path,
            expected_bytes=expected_bytes,
            is_cancelled=lambda: self._cancelled,
            register_proc=register,
            unregister_proc=unregister,
        )

    def _run(
        self,
        cmd: list[str],
        *,
        what: str,
        timeout: int | None = None,
        allow_nonzero: bool = False,
    ) -> None:
        code, stderr = self._run_capture(cmd, what=what, timeout=timeout)
        if self._cancelled:
            return
        if code != 0:
            detail = (stderr or '').strip() or f'exit {code}'
            if len(detail) > 400:
                detail = detail[:400] + '…'
            if allow_nonzero:
                log.warning('%s exited %s (will validate output): %s', what, code, detail)
                return
            raise RuntimeError(f'{what} failed: {detail}')

    def _run_capture(
        self,
        cmd: list[str],
        *,
        what: str,
        timeout: int | None = None,
    ) -> tuple[int, str]:
        log.info('Running %s: %s', what, ' '.join(cmd))
        proc: subprocess.Popen[str] | None = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=subprocess_env_for(cmd),
            )
            with self._proc_lock:
                self._procs.add(proc)
            try:
                _stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                raise RuntimeError(f'{what} timed out after {timeout}s') from None
            code = proc.returncode if proc is not None else -1
            return code if code is not None else -1, stderr or ''
        except OSError as exc:
            raise RuntimeError(f'Failed to run {what}: {exc}') from exc
        finally:
            if proc is not None:
                with self._proc_lock:
                    self._procs.discard(proc)

