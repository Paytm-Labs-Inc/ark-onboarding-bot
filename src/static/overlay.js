(function () {
  const config = window.__ARK_OVERLAY_CONFIG__ || {};
  const embedUrl = config.embedUrl || "embed";
  const label = config.label || "Ask about onboarding";

  const root = document.createElement("div");
  root.className = "ark-overlay-root";
  root.innerHTML =
    '<button type="button" class="ark-overlay-launcher" aria-label="' +
    label +
    '" aria-expanded="false" aria-controls="ark-overlay-panel">' +
    '<svg viewBox="0 0 100 100" aria-hidden="true" focusable="false">' +
    '<path d="M46 4 L46 80 L8 80 Z" fill="currentColor" opacity="0.95"/>' +
    '<path d="M54 20 L54 96 L92 96 Z" fill="currentColor" opacity="0.65"/>' +
    "</svg></button>" +
    '<section id="ark-overlay-panel" class="ark-overlay-panel" role="dialog" aria-label="Onboarding assistant">' +
    '<div class="ark-overlay-panel-header">' +
    '<div class="ark-overlay-panel-title">' +
    '<svg viewBox="0 0 100 100" aria-hidden="true" focusable="false">' +
    '<path d="M46 4 L46 80 L8 80 Z" fill="#5b46c0"/>' +
    '<path d="M54 20 L54 96 L92 96 Z" fill="#a794ea"/>' +
    "</svg><span>Onboarding assistant</span></div>" +
    '<button type="button" class="ark-overlay-close" aria-label="Close chat">&times;</button>' +
    "</div>" +
    '<iframe class="ark-overlay-frame" title="Ark onboarding chat" src="about:blank" loading="lazy"></iframe>' +
    "</section>";

  document.body.appendChild(root);

  const launcher = root.querySelector(".ark-overlay-launcher");
  const panel = root.querySelector(".ark-overlay-panel");
  const closeBtn = root.querySelector(".ark-overlay-close");
  const frame = root.querySelector(".ark-overlay-frame");
  let loaded = false;

  function openPanel() {
    if (!loaded) {
      frame.src = embedUrl;
      loaded = true;
    }
    root.classList.add("is-open");
    panel.classList.add("is-open");
    launcher.setAttribute("aria-expanded", "true");
  }

  function closePanel() {
    root.classList.remove("is-open");
    panel.classList.remove("is-open");
    launcher.setAttribute("aria-expanded", "false");
  }

  launcher.addEventListener("click", () => {
    if (panel.classList.contains("is-open")) {
      closePanel();
    } else {
      openPanel();
    }
  });

  closeBtn.addEventListener("click", closePanel);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && panel.classList.contains("is-open")) {
      closePanel();
    }
  });
})();
