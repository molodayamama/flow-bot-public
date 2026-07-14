(() => {
  "use strict";

  const video = document.querySelector("[data-showcase-video]");
  const toggle = document.querySelector("[data-video-toggle]");
  if (!(video instanceof HTMLVideoElement) || !(toggle instanceof HTMLButtonElement)) return;

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let pausedByUser = reducedMotion.matches;

  const render = () => {
    const paused = video.paused;
    toggle.dataset.state = paused ? "paused" : "playing";
    toggle.setAttribute("aria-pressed", paused ? "false" : "true");
    toggle.setAttribute("aria-label", paused ? "Воспроизвести пример видео" : "Приостановить пример видео");
  };

  const play = () => {
    const attempt = video.play();
    if (attempt && typeof attempt.catch === "function") attempt.catch(render);
  };

  const applyMotionPreference = () => {
    if (reducedMotion.matches) {
      pausedByUser = true;
      video.pause();
    }
    render();
  };

  toggle.addEventListener("click", () => {
    if (video.paused) {
      pausedByUser = false;
      play();
    } else {
      pausedByUser = true;
      video.pause();
    }
    render();
  });
  video.addEventListener("play", render);
  video.addEventListener("pause", render);
  video.addEventListener("ended", render);
  if (typeof reducedMotion.addEventListener === "function") {
    reducedMotion.addEventListener("change", applyMotionPreference);
  } else if (typeof reducedMotion.addListener === "function") {
    reducedMotion.addListener(applyMotionPreference);
  }

  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) video.pause();
      else if (!pausedByUser && !reducedMotion.matches) play();
    }, { threshold: 0.2 });
    observer.observe(video);
  }

  applyMotionPreference();
})();
