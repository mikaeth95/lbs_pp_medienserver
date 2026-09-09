"use strict";

const player = document.querySelector("#video-player");

if (player) {
  document.querySelectorAll("[data-seek]").forEach((button) => {
    button.addEventListener("click", () => {
      const offset = Number(button.dataset.seek);
      const target = Math.max(0, Math.min(player.duration || Infinity, player.currentTime + offset));
      player.currentTime = target;
    });
  });
}
