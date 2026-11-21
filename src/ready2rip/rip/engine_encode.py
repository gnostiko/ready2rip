# SPDX-License-Identifier: GPL-3.0-or-later
"""EncodeMixin methods for :class:`~ready2rip.rip.engine.RipEngine`."""

from __future__ import annotations

from ready2rip.rip._engine_shared import *  # noqa: F403

# NOTE: This mixin is composed onto RipEngine. Method bodies expect the full
# RipEngine attribute/helper surface (imports resolved in engine.py).


class EncodeMixin:
    def _missing_encoder(self, fmt: str) -> str | None:
        if fmt == 'flac' and not shutil.which('flac'):
            return 'flac encoder not found'
        if fmt == 'wavpack' and not shutil.which('wavpack') and not shutil.which('ffmpeg'):
            return 'wavpack or ffmpeg required for WavPack'
        if fmt == 'alac' and not shutil.which('ffmpeg'):
            return 'ffmpeg required for ALAC'
        if fmt == 'mp3' and not shutil.which('lame'):
            return 'lame encoder not found'
        if fmt == 'aac' and not shutil.which('ffmpeg'):
            return 'ffmpeg required for AAC'
        if fmt == 'qaac' and not qaac_available():
            return (
                'qaac not found — install Wine + qaac.exe + Apple AAC codecs '
                '(or set READY2RIP_QAAC)'
            )
        if fmt == 'aiff' and not shutil.which('ffmpeg'):
            return 'ffmpeg required for AIFF'
        if fmt == 'opus' and not shutil.which('opusenc') and not shutil.which('ffmpeg'):
            return 'opusenc or ffmpeg required for Opus'
        return None

    def _destinations_for_track(
        self,
        job: RipJob,
        track_number: int,
        *,
        filename: str | None = None,
    ) -> list[EncodeDest]:
        """Resolve album folder + output path(s) for primary (+ optional also)."""
        formats = encode_format_list(
            job.encode_format,
            also_encode=job.also_encode,
            also_format=job.also_encode_format,
            # Copy Image stays a single lossless/container encode.
            allow_also=not job.copy_image,
        )
        layout = (job.also_encode_folder or 'format_subdir').strip().lower()
        also_root = (job.also_encode_directory or '').strip()
        use_custom_also = len(formats) > 1 and layout == 'custom_folder' and also_root
        # Format subfolders when sharing a root — also when ALAC + AAC would
        # both write ``.m4a`` into the same folder (same_folder clash).
        ext_clash = False
        if len(formats) > 1 and not use_custom_also:
            p_ext = self.EXTENSIONS.get(formats[0], '')
            s_ext = self.EXTENSIONS.get(formats[1], '')
            ext_clash = bool(p_ext) and p_ext == s_ext
        multi = (
            len(formats) > 1
            and not use_custom_also
            and (layout == 'format_subdir' or ext_clash)
        )
        primary_base = Path(job.output_directory).expanduser().resolve()
        dests: list[EncodeDest] = []
        for i, enc_fmt in enumerate(formats):
            if enc_fmt not in self.EXTENSIONS:
                enc_fmt = 'flac'
            enc_ext = self.EXTENSIONS[enc_fmt]
            if i > 0 and use_custom_also:
                enc_base = Path(also_root).expanduser().resolve()
                folder_layout = 'same_folder'
            else:
                enc_base = primary_base
                if multi:
                    folder_layout = 'format_subdir'
                elif layout == 'same_folder' or use_custom_also:
                    folder_layout = 'same_folder'
                else:
                    # Unknown / empty custom path → keep both in the album folder.
                    folder_layout = 'same_folder'
            path_style = artist_style_for_container(
                job.artist_style,
                encode_format=enc_fmt,
                m4a_feat_artists=job.m4a_feat_artists,
            )
            meta_dir, templated = build_output_paths(
                base_dir=enc_base,
                album_folder_template=job.album_folder_template,
                filename_template=job.filename_template,
                album=job.album,
                track_number=max(1, track_number),
                extension=enc_ext,
                artist_style=path_style,
            )
            audio_dir = audio_dir_for_format(
                meta_dir,
                enc_fmt,
                folder_layout=folder_layout,
                multi_format=multi,
            )
            if filename:
                stem = Path(filename).name
                if stem.lower().endswith(enc_ext.lower()):
                    out_name = stem
                else:
                    # Strip a known audio suffix then apply this format's ext.
                    stem_path = Path(stem)
                    if stem_path.suffix.lower() in {
                        '.flac',
                        '.wav',
                        '.aiff',
                        '.aif',
                        '.wv',
                        '.mp3',
                        '.m4a',
                        '.opus',
                    }:
                        out_name = f'{stem_path.stem}{enc_ext}'
                    else:
                        out_name = f'{stem}{enc_ext}'
                out_path = audio_dir / out_name
            else:
                out_path = audio_dir / templated.name
            dests.append(
                EncodeDest(
                    format=enc_fmt,
                    base_dir=enc_base,
                    album_dir=audio_dir,
                    out_path=out_path,
                    meta_dir=meta_dir,
                    primary=i == 0,
                )
            )
        return apply_existing_files_policy(dests, job.existing_files)

    @staticmethod
    def _apply_existing_files_policy(
        dests: list[EncodeDest],
        policy: str | None,
    ) -> list[EncodeDest]:
        """Filter or rename destinations when output files already exist."""
        return apply_existing_files_policy(dests, policy)

    def planned_audio_paths(self, job: RipJob) -> list[Path]:
        """Templated audio destinations (overwrite paths) for conflict checks."""
        probe = replace(job, existing_files='overwrite')
        paths: list[Path] = []
        seen: set[Path] = set()
        if probe.copy_image:
            for dest in self._destinations_for_track(probe, 1):
                key = dest.out_path
                try:
                    key = dest.out_path.resolve()
                except OSError:
                    pass
                if key in seen:
                    continue
                seen.add(key)
                paths.append(dest.out_path)
            return paths
        for track_no in probe.track_numbers:
            for dest in self._destinations_for_track(probe, int(track_no)):
                key = dest.out_path
                try:
                    key = dest.out_path.resolve()
                except OSError:
                    pass
                if key in seen:
                    continue
                seen.add(key)
                paths.append(dest.out_path)
        return paths

    @staticmethod
    def conflicting_paths(paths: list[Path]) -> list[Path]:
        """Return existing files from *paths* (stable order)."""
        return plan_conflicting_paths(paths)

    def _ensure_destination_dirs(self, dests: list[EncodeDest]) -> None:
        """Create every destination album folder (and its base) up front."""
        for dest in dests:
            dest.base_dir.mkdir(parents=True, exist_ok=True)
            dest.meta_dir.mkdir(parents=True, exist_ok=True)
            dest.album_dir.mkdir(parents=True, exist_ok=True)

    def _encode_wav_to_destinations(
        self,
        wav_path: Path,
        dests: list[EncodeDest],
        job: RipJob,
        *,
        track_number: int,
        embed_art: EmbedArtworkSet,
        disc_track_count: int,
        retitle: str | None = None,
        write_cover: bool = True,
        tag_as_image: bool = False,
        on_progress: Callable[[int, str, Path], None] | None = None,
        on_activity: Callable[[str], None] | None = None,
    ) -> list[Path]:
        """Encode *wav_path* to the destination(s); return output paths in order.

        Tags and optional embedded art are applied after each encode. Folder
        cover is written once per album directory when missing. When
        *tag_as_image* is True, tags are album-level (no track/ISRC).
        """
        # Compressed encodes first so a final WAV dest can rename the temp.
        compressed = [d for d in dests if d.format != 'wav']
        wav_dests = [d for d in dests if d.format == 'wav']
        ordered = compressed + wav_dests
        outputs_by_dest: dict[int, Path] = {}
        n = max(1, len(ordered))
        covered: set[Path] = set()
        wav_left = len(wav_dests)

        for dest in ordered:
            dest.album_dir.mkdir(parents=True, exist_ok=True)
            dest.meta_dir.mkdir(parents=True, exist_ok=True)
            cover_dir = dest.meta_dir
            if (
                write_cover
                and job.save_cover_file
                and is_lossless_format(dest.format)
                and cover_dir not in covered
                and (
                    job.folder_artwork is not None
                    or job.artwork is not None
                    or job.artwork_lossless is not None
                )
                and not (cover_dir / 'cover.jpg').is_file()
                and not (cover_dir / 'cover.png').is_file()
            ):
                _prepare_artwork(job, cover_dir)
                covered.add(cover_dir)

        def _run_encode(dest: EncodeDest) -> EncodeDest:
            if self._cancelled:
                raise RuntimeError('Rip cancelled')
            self._encode(dest.format, wav_path, dest.out_path, job)
            return dest

        # Parallel primary + also-encode (read-only wav_path; tag serially after).
        if len(compressed) > 1:
            label = (
                f'Encoding track {track_number}…'
                if track_number > 0
                else 'Encoding…'
            )

            def _enc_tick() -> None:
                if on_activity is not None:
                    on_activity(label)

            with ProgressPulse(_enc_tick):
                workers = min(len(compressed), 2)
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futs = {
                        pool.submit(_run_encode, d): (i, d)
                        for i, d in enumerate(compressed)
                    }
                    try:
                        for fut in as_completed(futs):
                            if self._cancelled:
                                for pending in futs:
                                    pending.cancel()
                                raise RuntimeError('Rip cancelled')
                            dest = fut.result()
                            i, _ = futs[fut]
                            if on_progress is not None:
                                on_progress(i, dest.format, dest.out_path)
                    except Exception:
                        if self._cancelled:
                            pool.shutdown(wait=False, cancel_futures=True)
                        raise
        else:
            for dest in compressed:
                if on_progress is not None:
                    on_progress(0, dest.format, dest.out_path)
                label = (
                    f'Encoding track {track_number} to {dest.format.upper()}…'
                    if track_number > 0
                    else f'Encoding to {dest.format.upper()}…'
                )

                def _enc_tick(*, _label=label) -> None:
                    if on_activity is not None:
                        on_activity(_label)

                with ProgressPulse(_enc_tick):
                    _run_encode(dest)

        for i, dest in enumerate(ordered):
            if on_progress is not None and dest.format == 'wav':
                on_progress(i, dest.format, dest.out_path)
            if dest.format == 'wav':
                wav_left -= 1
                place_wav_output(wav_path, dest.out_path, move=wav_left == 0)

            if job.album is not None:
                total_for_tags = len(job.album.tracks) if job.album.tracks else disc_track_count
                lyrics_on = (
                    bool(job.embed_lyrics) and is_lossless_format(dest.format) and not tag_as_image
                )
                write_lyrics_tags = lyrics_on and lyrics_mode_writes_tags(job.lyrics_save_mode)
                write_lyrics_files = lyrics_on and lyrics_mode_writes_sidecar(job.lyrics_save_mode)
                self._tags.write_album_tags(
                    dest.out_path,
                    job.album,
                    track_number,
                    total_tracks=total_for_tags or None,
                    artist_style=job.artist_style,
                    m4a_feat_artists=job.m4a_feat_artists,
                    image=tag_as_image,
                    encoder=encoder_tag_for_format(dest.format),
                    encoded_by=(ripper_tag_value() if job.add_ripper_tag else None),
                    embed_lyrics=write_lyrics_tags,
                )
                if write_lyrics_files and track_number > 0:
                    write_lyrics_sidecar(
                        dest.out_path,
                        track_meta_for(job.album, track_number),
                    )
                if retitle and not tag_as_image:
                    _retag_title(dest.out_path, retitle, track_number=track_number)
            art = embed_art.for_format(dest.format, job)
            if art is not None:
                self._tags.embed_artwork(dest.out_path, art.data, art.mime)
            outputs_by_dest[id(dest)] = dest.out_path
            log.info(
                'Encoded %s → %s (%s/%s)',
                dest.format,
                dest.out_path,
                i + 1,
                n,
            )
        return [outputs_by_dest[id(d)] for d in dests]

    def _encode(self, fmt: str, wav_path: Path, out_path: Path, job: RipJob) -> None:
        """Encode with stock encoder presets; PCM is not pre-processed."""
        if out_path.exists():
            out_path.unlink()

        if fmt == 'flac':
            self._run(
                flac_encode_argv(wav_path, out_path, job.flac_compression),
                what='flac',
            )
        elif fmt == 'mp3':
            self._run(
                mp3_encode_argv(wav_path, out_path, job.mp3_quality),
                what='lame',
            )
        elif fmt == 'aac':
            quality = job.aac_quality or (
                f'cbr{int(job.aac_bitrate)}' if job.aac_bitrate else 'cbr256'
            )
            self._run(
                aac_encode_argv(wav_path, out_path, quality),
                what='ffmpeg aac',
            )
        elif fmt == 'qaac':
            self._run(
                qaac_encode_argv(
                    wav_path,
                    out_path,
                    job.qaac_quality or 'cvbr256',
                ),
                what='qaac',
            )
        elif fmt == 'opus':
            if shutil.which('opusenc'):
                self._run(
                    opus_encode_argv(wav_path, out_path, job.opus_bitrate),
                    what='opusenc',
                )
            else:
                self._run(
                    opus_ffmpeg_argv(wav_path, out_path, job.opus_bitrate),
                    what='ffmpeg opus',
                )
        elif fmt == 'aiff':
            self._run(
                aiff_encode_argv(wav_path, out_path),
                what='ffmpeg aiff',
            )
        elif fmt == 'wavpack':
            level = job.wavpack_compression or 'high'
            if shutil.which('wavpack'):
                self._run(
                    wavpack_encode_argv(wav_path, out_path, level),
                    what='wavpack',
                )
            else:
                self._run(
                    wavpack_ffmpeg_argv(wav_path, out_path, level),
                    what='ffmpeg wavpack',
                )
        elif fmt == 'alac':
            self._run(
                alac_encode_argv(wav_path, out_path),
                what='ffmpeg alac',
            )
        else:
            raise RuntimeError(f'Unsupported encode format: {fmt}')

        if not out_path.is_file() or out_path.stat().st_size < 100:
            raise RuntimeError(f'Encoder produced no output: {out_path}')

