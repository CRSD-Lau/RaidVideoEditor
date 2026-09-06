(() => {
  const DURATION_MS = 30 * 60 * 1000;
  const output = document.querySelector("[data-countdown]");
  let endAt = performance.now() + DURATION_MS;
  let animationFrame = null;

  function resetCountdown() {
    endAt = performance.now() + DURATION_MS;
    if (animationFrame === null) {
      animationFrame = requestAnimationFrame(render);
    }
  }

  function render(now) {
    const remainingMs = Math.max(0, endAt - now);
    const totalSeconds = Math.ceil(remainingMs / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;

    output.textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    output.setAttribute("aria-label", `${minutes} minutes ${seconds} seconds remaining`);

    if (remainingMs > 0) {
      animationFrame = requestAnimationFrame(render);
    } else {
      animationFrame = null;
    }
  }

  window.addEventListener("obsSourceActiveChanged", (event) => {
    if (event.detail?.active) {
      resetCountdown();
    }
  });
  window.addEventListener("obsSourceVisibleChanged", (event) => {
    if (event.detail?.visible) {
      resetCountdown();
    }
  });

  animationFrame = requestAnimationFrame(render);
})();
