(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const dialog = $("#new-job-dialog");
  const soundToggle = $("#sound-toggle");
  const toastStack = $("#toast-stack");
  const providerSelect = $("#provider-select");
  const providerHint = $("#provider-hint");
  const bookingUrlInput = $("#booking-url");
  const bookingUrlRequired = $("#booking-url-required");
  const releaseInput = $("#release-at");
  const releaseInputField = $("#release-at-field");
  const quickRelease = $("#quick-release");
  const bmsInput = $("#bms-url");
  const bmsPreview = $("#bms-preview");
  const showtimeMode = $("#showtime-mode");
  const exactTimeField = $("#exact-time-field");
  const windowStartField = $("#window-start-field");
  const windowEndField = $("#window-end-field");
  const showDateInput = $("#show-date-input");

  let audioContext = null;
  let soundEnabled = localStorage.getItem("ticket-watch-sound") !== "off";
  let refreshTimer = null;
  let refreshInFlight = false;

  const seenTriggered = new Set(
    $$('[data-job-status="triggered"]').map((card) => card.dataset.jobId)
  );

  /* ------------------------------------------------------------ dialog */

  function openDialog() {
    if (dialog && !dialog.open) dialog.showModal();
  }

  function closeDialog() {
    if (dialog && dialog.open) dialog.close();
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-open-dialog], #open-job-dialog, #empty-new-job");
    if (trigger) openDialog();
  });

  $("#close-job-dialog")?.addEventListener("click", closeDialog);
  $("#cancel-job-dialog")?.addEventListener("click", closeDialog);
  dialog?.addEventListener("click", (event) => {
    if (event.target === dialog) closeDialog();
  });

  if (dialog?.dataset.open === "true") {
    openDialog();
  }

  // Topbar "New watch" on guide/help pages links back here with ?new=1.
  const pageParams = new URLSearchParams(window.location.search);
  if (pageParams.get("new") === "1") {
    openDialog();
    pageParams.delete("new");
    const remaining = pageParams.toString();
    history.replaceState(
      null,
      "",
      `${window.location.pathname}${remaining ? `?${remaining}` : ""}${window.location.hash}`
    );
  }

  /* ------------------------------------------------------------ provider fields */

  function updateProviderFields() {
    if (!providerSelect) return;
    const option = providerSelect.selectedOptions[0];
    if (!option) return;
    const requiresUrl = option.dataset.requiresUrl === "true";
    if (providerHint) providerHint.textContent = option.dataset.description || "";
    if (bookingUrlInput) {
      bookingUrlInput.required = requiresUrl && !(bmsInput && bmsInput.value.trim());
      bookingUrlInput.placeholder = requiresUrl
        ? "https://in.bookmyshow.com/..."
        : "Optional — a demo link is generated";
    }
    if (bookingUrlRequired) {
      bookingUrlRequired.textContent = requiresUrl ? "required" : "optional for mock";
    }
  }

  providerSelect?.addEventListener("change", updateProviderFields);
  bmsInput?.addEventListener("input", updateProviderFields);
  updateProviderFields();

  /* ------------------------------------------------------------ release shortcut */

  function toLocalInput(date) {
    const pad = (value) => String(value).padStart(2, "0");
    return [
      date.getFullYear(),
      "-",
      pad(date.getMonth() + 1),
      "-",
      pad(date.getDate()),
      "T",
      pad(date.getHours()),
      ":",
      pad(date.getMinutes()),
      ":",
      pad(date.getSeconds()),
    ].join("");
  }

  quickRelease?.addEventListener("click", () => {
    if (!releaseInput) return;
    const atMode = $('input[name="release_mode"][value="at"]');
    if (atMode) {
      atMode.checked = true;
      updateReleaseFields();
    }
    releaseInput.value = toLocalInput(new Date(Date.now() + 15000));
    releaseInput.focus();
    releaseInput.dispatchEvent(new Event("input", { bubbles: true }));
  });

  /* ------------------------------------------------------------ BMS link parser */

  function humanizeSlug(slug) {
    if (!slug) return "";
    const small = new Set(["a", "an", "and", "at", "for", "in", "of", "on", "the", "to"]);
    return decodeURIComponent(slug)
      .replace(/[-_]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .split(" ")
      .map((word, index) => {
        if (index && small.has(word.toLowerCase())) return word.toLowerCase();
        return word.charAt(0).toUpperCase() + word.slice(1);
      })
      .join(" ");
  }

  function cityLabel(city) {
    const labels = {
      hyd: "Hyderabad",
      blr: "Bengaluru",
      bom: "Mumbai",
      del: "Delhi",
      che: "Chennai",
      kol: "Kolkata",
      pnq: "Pune",
    };
    const key = String(city || "").toLowerCase();
    return labels[key] || key.replace(/-/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function parseBmsUrl(raw) {
    if (!raw) return null;
    let url;
    try {
      url = new URL(/^https?:\/\//i.test(raw) ? raw : `https://${raw}`);
    } catch (error) {
      return null;
    }
    if (!/(^|\.)bookmyshow\.com$/i.test(url.hostname)) return null;
    const path = url.pathname.replace(/\/+$/, "");

    let match = path.match(
      /^\/movies\/([^/]+)\/seat-layout\/([A-Za-z]{2,4}\d{4,})\/([A-Za-z0-9]+)\/(\d+)\/(\d{8})$/
    );
    if (match) {
      return {
        kind: "seat-layout",
        city: decodeURIComponent(match[1]).toLowerCase(),
        event: match[2].toUpperCase(),
        venue: match[3].toUpperCase(),
        session: match[4],
        date: match[5],
        slug: "",
      };
    }

    match = path.match(
      /^\/movies\/([^/]+)\/([^/]+)\/buytickets\/([A-Za-z]{2,4}\d{4,})\/(\d{8})$/
    );
    if (match) {
      return {
        kind: "showtimes",
        city: decodeURIComponent(match[1]).toLowerCase(),
        slug: decodeURIComponent(match[2]),
        event: match[3].toUpperCase(),
        venue: "",
        session: "",
        date: match[4],
      };
    }

    match = path.match(/^\/movies\/([^/]+)\/([^/]+)\/([A-Za-z]{2,4}\d{4,})$/);
    if (match) {
      return {
        kind: "movie",
        city: decodeURIComponent(match[1]).toLowerCase(),
        slug: decodeURIComponent(match[2]),
        event: match[3].toUpperCase(),
        venue: "",
        session: "",
        date: "",
      };
    }
    return null;
  }

  function formatBmsDate(value) {
    if (!value || value.length !== 8) return "";
    const year = value.slice(0, 4);
    const month = value.slice(4, 6);
    const day = value.slice(6, 8);
    const parsed = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
    if (Number.isNaN(parsed.getTime())) return "";
    return parsed.toLocaleDateString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    });
  }

  function fillIfEmpty(selector, value) {
    const element = $(selector);
    if (!element || !value) return;
    if (!element.value.trim()) element.value = value;
  }

  function setField(selector, value) {
    const element = $(selector);
    if (element && value) element.value = value;
  }

  function updateBmsPreview() {
    if (!bmsInput) return;
    const parsed = parseBmsUrl(bmsInput.value.trim());
    if (!parsed) {
      if (bmsPreview) bmsPreview.hidden = true;
      return;
    }
    if (bmsPreview) bmsPreview.hidden = false;
    const movie = parsed.slug ? humanizeSlug(parsed.slug) : "—";
    const city = cityLabel(parsed.city);
    const showDate = formatBmsDate(parsed.date);
    const dateValue = parsed.date
      ? `${parsed.date.slice(0, 4)}-${parsed.date.slice(4, 6)}-${parsed.date.slice(6, 8)}`
      : "";

    $("#preview-movie").textContent = movie || "—";
    $("#preview-city").textContent = city || "—";
    $("#preview-event").textContent = parsed.event || "—";
    $("#preview-venue").textContent = parsed.venue || "—";
    $("#preview-date").textContent = showDate || "—";
    $("#preview-session").textContent = parsed.session || "—";

    setField("#movie-id-input", parsed.event);
    if (parsed.venue) setField("#venue-id-input", parsed.venue);
    fillIfEmpty("#city-input", parsed.city);
    if (parsed.venue) fillIfEmpty("#venue-input", parsed.venue);
    else fillIfEmpty("#venue-input", `Any theatre in ${city}`);
    if (dateValue) fillIfEmpty("#show-date-input", dateValue);
    if (parsed.slug) fillIfEmpty("#job-name", humanizeSlug(parsed.slug));
  }

  bmsInput?.addEventListener("input", updateBmsPreview);
  bmsInput?.addEventListener("change", updateBmsPreview);
  updateBmsPreview();

  $("#bms-example")?.addEventListener("click", () => {
    if (!bmsInput) return;
    bmsInput.value =
      "https://in.bookmyshow.com/movies/hyderabad/seat-layout/ET00521930/ALUC/6296/20261022";
    bmsInput.dispatchEvent(new Event("input", { bubbles: true }));
    bmsInput.focus();
  });

  /* ------------------------------------------------------------ showtime / release modes */

  function updateShowtimeFields() {
    const mode = showtimeMode?.value || "any";
    if (exactTimeField) exactTimeField.hidden = mode !== "exact";
    if (windowStartField) windowStartField.hidden = mode !== "window";
    if (windowEndField) windowEndField.hidden = mode !== "window";

    const exactInput = $("#exact-time-field input");
    const startInput = $("#window-start-field input");
    const endInput = $("#window-end-field input");
    if (exactInput) exactInput.required = mode === "exact";
    if (startInput) startInput.required = mode === "window";
    if (endInput) endInput.required = mode === "window";
    if (showDateInput) showDateInput.required = mode === "exact";
  }

  function updateReleaseFields() {
    const selected = $('input[name="release_mode"]:checked');
    const startNow = selected?.value === "now";
    if (releaseInputField) releaseInputField.hidden = startNow;
    if (releaseInput) releaseInput.required = !startNow;
  }

  showtimeMode?.addEventListener("change", updateShowtimeFields);
  $$('input[name="release_mode"]').forEach((radio) => {
    radio.addEventListener("change", updateReleaseFields);
  });
  updateShowtimeFields();
  updateReleaseFields();

  dialog?.querySelector("form")?.addEventListener("submit", () => {
    if (bookingUrlInput && !bookingUrlInput.value.trim() && bmsInput?.value.trim()) {
      bookingUrlInput.value = bmsInput.value.trim();
    }
  });

  /* ------------------------------------------------------------ sound */

  function ensureAudio() {
    const AudioCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtor) return null;
    if (!audioContext) audioContext = new AudioCtor();
    if (audioContext.state === "suspended") audioContext.resume();
    return audioContext;
  }

  function beep(frequency = 880, duration = 0.18, delay = 0) {
    const ctx = ensureAudio();
    if (!ctx) return;
    const start = ctx.currentTime + delay;
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = frequency;
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(0.35, start + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    oscillator.connect(gain);
    gain.connect(ctx.destination);
    oscillator.start(start);
    oscillator.stop(start + duration + 0.05);
  }

  function playAlert() {
    if (!soundEnabled) return;
    ensureAudio();
    beep(880, 0.18, 0);
    beep(880, 0.18, 0.24);
    beep(1180, 0.28, 0.48);
    if (navigator.vibrate) navigator.vibrate([120, 60, 120]);
  }

  function renderSoundButton() {
    if (!soundToggle) return;
    soundToggle.textContent = soundEnabled ? "🔔" : "🔕";
    soundToggle.classList.toggle("is-off", !soundEnabled);
    soundToggle.setAttribute("aria-pressed", String(soundEnabled));
    soundToggle.title = soundEnabled
      ? "Alert sound is on — click to mute"
      : "Alert sound is off — click to enable";
  }

  soundToggle?.addEventListener("click", () => {
    soundEnabled = !soundEnabled;
    localStorage.setItem("ticket-watch-sound", soundEnabled ? "on" : "off");
    renderSoundButton();
    if (soundEnabled) {
      ensureAudio();
      beep(980, 0.12);
      showToast("Sound on", "You will hear an alert when a watch fires.", "info");
    }
  });

  renderSoundButton();

  // Browsers require a gesture before audio can play. Unlock on the first one.
  document.addEventListener(
    "pointerdown",
    () => {
      if (soundEnabled) ensureAudio();
    },
    { once: true }
  );

  /* ------------------------------------------------------------ toasts */

  function showToast(title, message, kind = "info") {
    if (!toastStack) return;
    const toast = document.createElement("div");
    toast.className = `toast toast-${kind}`;
    const heading = document.createElement("strong");
    heading.textContent = title;
    const copy = document.createElement("span");
    copy.textContent = message;
    toast.append(heading, copy);
    toastStack.appendChild(toast);
    window.setTimeout(() => toast.remove(), 9000);
  }

  /* ------------------------------------------------------------ countdowns */

  function updateCountdowns() {
    const now = Date.now();
    $$("[data-countdown]").forEach((element) => {
      const raw = element.dataset.countdown;
      if (!raw) return;
      const target = new Date(raw).getTime();
      if (Number.isNaN(target)) return;
      const delta = target - now;
      if (delta <= 0) {
        element.textContent = "release window open";
        element.classList.add("is-live");
        return;
      }
      element.classList.remove("is-live");
      const seconds = Math.floor(delta / 1000);
      const days = Math.floor(seconds / 86400);
      const hours = Math.floor((seconds % 86400) / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);
      const secs = seconds % 60;
      if (days > 0) element.textContent = `${days}d ${hours}h`;
      else if (hours > 0) element.textContent = `${hours}h ${minutes}m`;
      else if (minutes > 0) element.textContent = `${minutes}m ${secs}s`;
      else element.textContent = `${secs}s`;
    });
  }

  window.setInterval(updateCountdowns, 1000);
  updateCountdowns();

  /* ------------------------------------------------------------ dashboard refresh */

  function detectNewTriggers() {
    $$("[data-job-id]").forEach((card) => {
      if (card.dataset.jobStatus !== "triggered") return;
      const id = card.dataset.jobId;
      if (!id || seenTriggered.has(id)) return;
      seenTriggered.add(id);
      const name = $(".job-name", card)?.textContent?.trim() || "A watch";
      playAlert();
      showToast("Action needed", `${name} needs a handoff.`, "live");
    });
  }

  async function refreshDashboard() {
    if (refreshInFlight) return;
    refreshInFlight = true;
    try {
      const [dashboardResponse, activityResponse] = await Promise.all([
        fetch("/partials/dashboard", { headers: { "X-Requested-With": "fetch" } }),
        fetch("/partials/activity", { headers: { "X-Requested-With": "fetch" } }),
      ]);
      if (dashboardResponse.ok) {
        $("#dashboard-region").innerHTML = await dashboardResponse.text();
        detectNewTriggers();
        updateCountdowns();
      }
      if (activityResponse.ok) {
        $("#activity-region").innerHTML = await activityResponse.text();
      }
    } catch (error) {
      // The SSE connection or next poll will retry. Keep the UI usable offline.
      console.debug("Dashboard refresh failed", error);
    } finally {
      refreshInFlight = false;
    }
  }

  function scheduleRefresh() {
    if (refreshTimer) return;
    refreshTimer = window.setTimeout(() => {
      refreshTimer = null;
      refreshDashboard();
    }, 250);
  }

  window.setInterval(refreshDashboard, 5000);

  /* ------------------------------------------------------------ SSE */

  if (window.EventSource) {
    const source = new EventSource("/api/events");
    source.addEventListener("update", (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "job.triggered" && data.job_id && !seenTriggered.has(data.job_id)) {
          seenTriggered.add(data.job_id);
          playAlert();
          showToast("Action needed", data.message || "Open BMS and check the latest alert.", "live");
        }
      } catch (error) {
        console.debug("Bad SSE payload", error);
      }
      scheduleRefresh();
    });
    source.addEventListener("error", () => {
      // EventSource reconnects automatically; polling keeps the UI fresh meanwhile.
    });
  }

  /* ------------------------------------------------------------ confirms */

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-confirm]");
    if (!form) return;
    const message = form.dataset.confirm || "Are you sure?";
    if (!window.confirm(message)) event.preventDefault();
  });
})();
