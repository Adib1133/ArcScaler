"use strict";
const descriptions = {
  library: "Library — a game list alongside independent Super Resolution and Frame Generation controls.",
  downloads: "Downloads — browse releases, manage local packages and choose a version.",
  hardware: "Hardware — inspect the detected GPU and suggested settings.",
  "app-settings": "Preferences — interface scale, reduced motion and local diagnostics."
};
const image = document.getElementById("app-image");
document.querySelectorAll("[data-view]").forEach(button => {
  button.addEventListener("click", () => {
    const view = button.dataset.view;
    image.src = `assets/${view}.png`;
    image.alt = `ArcScaler ${descriptions[view]}`;
    document.getElementById("image-link").href = image.src;
    document.getElementById("capture-text").textContent = descriptions[view];
    document.querySelectorAll("[data-view]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
  });
});
