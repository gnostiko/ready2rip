(() => {
  const $ = (id) => document.getElementById(id);

  // Fallback catalog (matches Melton --help) so controls paint before /api/catalog returns.
  const FALLBACK_CATALOG = {
    tools: [
      { id: "transcode", label: "transcode-video.rb", default: true },
      { id: "detect_crop", label: "detect-crop.rb" },
      { id: "convert", label: "convert-video.rb" },
    ],
    video_modes: [
      { id: "h264", label: "h264 (default) — x264 two-pass CBR", default: true },
      { id: "hevc", label: "hevc — x265_10bit CQ (slow, 4K HDR)" },
      { id: "nvenc-hevc", label: "nvenc-hevc — Nvidia HEVC 10-bit" },
      { id: "av1", label: "av1 — svt_av1_10bit (audio defaults to opus)" },
      { id: "nvenc-av1", label: "nvenc-av1 — Nvidia AV1 (audio defaults to opus)" },
      { id: "none", label: "none — no video encode (HandBrake still invoked)" },
    ],
    audio_modes: [
      {
        id: "default",
        label: "Melton default (aac; opus for av1 / nvenc-av1)",
        default: true,
      },
      { id: "aac", label: "aac" },
      { id: "opus", label: "opus" },
      { id: "eac3", label: "eac3" },
      { id: "none", label: "none" },
    ],
    aac_encoders: [
      { id: "av_aac", label: "av_aac (default)", default: true },
      { id: "fdk_aac", label: "fdk_aac" },
      { id: "ca_aac", label: "ca_aac" },
    ],
    burn_subtitle: [
      { id: "auto", label: "automatic (default — forced track)", default: true },
      { id: "none", label: "none" },
    ],
    containers: [
      { id: "default", label: "Matroska .mkv (Melton default)", default: true },
      { id: "av_mp4", label: "MP4 via -x format=av_mp4" },
      { id: "av_webm", label: "WebM via -x format=av_webm" },
      { id: "av_mkv", label: "MKV via -x format=av_mkv" },
    ],
    crop_modes: [
      { id: "conservative", label: "conservative (default)", default: true },
      { id: "auto", label: "auto" },
    ],
  };

  const els = {
    deps: $("deps"),
    drop: $("drop"),
    inputPath: $("input-path"),
    probeSummary: $("probe-summary"),
    tool: $("tool"),
    mode: $("mode"),
    container: $("container"),
    preset: $("preset"),
    bitrate: $("bitrate"),
    quality: $("quality"),
    noBframe: $("no-bframe-refs"),
    audioMode: $("audio-mode"),
    audioTrack: $("audio-track"),
    audioLang: $("audio-lang"),
    aacEncoder: $("aac-encoder"),
    ac3: $("ac3-surround"),
    burn: $("burn-subtitle"),
    subTrack: $("sub-track"),
    subLang: $("sub-lang"),
    cropMode: $("crop-mode"),
    outputDir: $("output-dir"),
    extra: $("extra"),
    dryRun: $("dry-run"),
    debug: $("debug"),
    form: $("form"),
    log: $("log"),
    fsTranscode: $("fs-transcode"),
    fsAudio: $("fs-audio"),
    fsSubs: $("fs-subs"),
    fsCrop: $("fs-crop"),
  };

  let probe = null;
  let pollTimer = null;

  function fillSelect(sel, items, valueKey = "id", labelKey = "label") {
    sel.innerHTML = "";
    for (const item of items) {
      const opt = document.createElement("option");
      opt.value = item[valueKey];
      opt.textContent = item[labelKey];
      if (item.default) opt.selected = true;
      sel.appendChild(opt);
    }
  }

  function setLog(text) {
    els.log.textContent = text;
  }

  async function api(path, body) {
    const opts = body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : { method: "GET" };
    const res = await fetch(path, opts);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  function toolVisibility() {
    const tool = els.tool.value;
    const isTranscode = tool === "transcode";
    const isCrop = tool === "detect_crop";
    els.fsTranscode.hidden = !isTranscode;
    els.fsAudio.hidden = !isTranscode;
    els.fsSubs.hidden = !isTranscode;
    els.fsCrop.hidden = !isCrop;
  }

  function resetTrackSelects() {
    els.audioTrack.innerHTML = "";
    els.subTrack.innerHTML = "";
    const aDef = document.createElement("option");
    aDef.value = "";
    aDef.textContent = "Melton default (track 1)";
    els.audioTrack.appendChild(aDef);
    const sDef = document.createElement("option");
    sDef.value = "";
    sDef.textContent = "— none (use burn / Melton auto)";
    els.subTrack.appendChild(sDef);
  }

  function applyProbe(data) {
    probe = data;
    resetTrackSelects();
    for (const a of data.audio || []) {
      const opt = document.createElement("option");
      opt.value = String(a.index);
      opt.textContent = a.label;
      els.audioTrack.appendChild(opt);
    }
    for (const s of data.subtitles || []) {
      const opt = document.createElement("option");
      opt.value = String(s.index);
      opt.textContent = s.label;
      els.subTrack.appendChild(opt);
      const burnOpt = document.createElement("option");
      burnOpt.value = String(s.index);
      burnOpt.textContent = `burn track ${s.index}: ${s.label}`;
      // keep auto/none; append track choices after
      els.burn.appendChild(burnOpt);
    }
    els.audioLang.innerHTML = "";
    els.subLang.innerHTML = "";
    for (const lang of data.languages || []) {
      const a = document.createElement("option");
      a.value = lang.code;
      a.textContent = lang.label;
      els.audioLang.appendChild(a);
      const s = document.createElement("option");
      s.value = lang.code;
      s.textContent = lang.label;
      els.subLang.appendChild(s);
    }
    const bits = [
      data.filename,
      data.video?.[0]
        ? `${data.video[0].width}×${data.video[0].height} ${data.video[0].codec}`
        : null,
      `${(data.audio || []).length} audio`,
      `${(data.subtitles || []).length} subs`,
    ].filter(Boolean);
    els.probeSummary.textContent = bits.join(" · ");
  }

  function collectPayload() {
    const path = els.inputPath.value.trim();
    const extra = els.extra.value
      .split(/\n+/)
      .map((s) => s.trim())
      .filter(Boolean);

    const addAudio = [];
    if (els.audioTrack.value) addAudio.push(els.audioTrack.value);
    if (els.audioLang.value) addAudio.push(els.audioLang.value);

    const addSubtitle = [];
    if (els.subTrack.value) addSubtitle.push(els.subTrack.value);
    if (els.subLang.value) addSubtitle.push(els.subLang.value);

    return {
      tool: els.tool.value,
      inputs: path ? [path] : [],
      output_dir: els.outputDir.value.trim() || null,
      dry_run: els.dryRun.checked,
      debug: els.debug.checked,
      mode: els.mode.value,
      preset: els.preset.value.trim() || null,
      bitrate: els.bitrate.value || null,
      quality: els.quality.value || null,
      no_bframe_refs: els.noBframe.checked,
      audio_mode: els.audioMode.value,
      add_audio: addAudio,
      ac3_surround: els.ac3.checked,
      aac_encoder: els.aacEncoder.value,
      burn_subtitle: els.burn.value,
      add_subtitle: addSubtitle,
      container: els.container.value,
      extra,
      crop_mode: els.cropMode.value,
    };
  }

  async function loadDeps() {
    const deps = await api("/api/deps");
    els.deps.innerHTML = "";
    for (const d of deps) {
      const li = document.createElement("li");
      const optional = d.name === "mkvpropedit";
      const cls = d.ok ? "ok" : optional ? "opt" : "bad";
      li.innerHTML = `<span>${d.name}</span><span class="${cls}">${
        d.ok ? "OK" : optional ? "optional" : "MISSING"
      }</span><span>${d.path || "—"}</span>`;
      els.deps.appendChild(li);
    }
  }

  function applyCatalog(cat) {
    fillSelect(els.tool, cat.tools);
    fillSelect(els.mode, cat.video_modes);
    fillSelect(els.container, cat.containers);
    fillSelect(els.audioMode, cat.audio_modes);
    fillSelect(els.aacEncoder, cat.aac_encoders);
    fillSelect(els.burn, cat.burn_subtitle);
    fillSelect(els.cropMode, cat.crop_modes);
    resetTrackSelects();
    els.audioLang.innerHTML =
      '<option value="">— (use track number / Melton default)</option>';
    els.subLang.innerHTML = '<option value="">— none</option>';
    toolVisibility();
  }

  async function loadCatalog() {
    applyCatalog(FALLBACK_CATALOG);
    const cat = await api("/api/catalog");
    applyCatalog(cat);
  }

  async function doProbe() {
    const path = els.inputPath.value.trim();
    if (!path) {
      setLog("Enter an absolute path to a media file, then Probe.");
      return;
    }
    // Reset burn options to catalog defaults before re-adding tracks
    const cat = await api("/api/catalog");
    fillSelect(els.burn, cat.burn_subtitle);
    try {
      const data = await api("/api/probe", { path });
      applyProbe(data);
      setLog(`Probed ${data.path}`);
    } catch (err) {
      setLog(`Probe failed: ${err.message}`);
    }
  }

  async function preview() {
    try {
      const data = await api("/api/preview", collectPayload());
      setLog(
        [
          data.cwd ? `cwd: ${data.cwd}` : "cwd: (process default)",
          data.command,
        ].join("\n")
      );
    } catch (err) {
      setLog(`Preview failed: ${err.message}`);
    }
  }

  async function pollJob(id) {
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const job = await api(`/api/jobs/${id}`);
        const parts = [
          `status: ${job.status}`,
          job.cwd ? `cwd: ${job.cwd}` : null,
          job.command,
          "",
          job.stderr || "",
          job.stdout || "",
        ].filter((x) => x !== null);
        setLog(parts.join("\n"));
        if (job.status !== "running") clearInterval(pollTimer);
      } catch (err) {
        clearInterval(pollTimer);
        setLog(`Job poll failed: ${err.message}`);
      }
    }, 800);
  }

  async function runJob(ev) {
    ev.preventDefault();
    try {
      const job = await api("/api/run", collectPayload());
      setLog(`Started ${job.id}\n${job.command}`);
      pollJob(job.id);
    } catch (err) {
      setLog(`Run failed: ${err.message}`);
    }
  }

  // Drop / paste support: browsers cannot expose local paths from File objects
  // for security, so we accept drag of text/uri-list or typed paths.
  els.drop.addEventListener("dragover", (e) => {
    e.preventDefault();
    els.drop.classList.add("active");
  });
  els.drop.addEventListener("dragleave", () => els.drop.classList.remove("active"));
  els.drop.addEventListener("drop", (e) => {
    e.preventDefault();
    els.drop.classList.remove("active");
    const uri = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
    if (uri) {
      let path = uri.trim().split("\n")[0];
      if (path.startsWith("file://")) {
        try {
          path = decodeURIComponent(new URL(path).pathname);
        } catch (_) {
          /* keep raw */
        }
      }
      els.inputPath.value = path;
      doProbe();
      return;
    }
    const file = e.dataTransfer.files?.[0];
    if (file?.path) {
      // Chromium/Electron sometimes exposes .path
      els.inputPath.value = file.path;
      doProbe();
      return;
    }
    if (file?.name) {
      setLog(
        `Browser hid the local path for “${file.name}”. Paste the absolute path into the field and Probe.`
      );
    }
  });

  $("btn-probe").addEventListener("click", doProbe);
  $("btn-preview").addEventListener("click", preview);
  els.form.addEventListener("submit", runJob);
  els.tool.addEventListener("change", toolVisibility);
  els.inputPath.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      doProbe();
    }
  });

  Promise.all([loadDeps(), loadCatalog()]).catch((err) => {
    setLog(`Startup failed: ${err.message}`);
  });
})();
