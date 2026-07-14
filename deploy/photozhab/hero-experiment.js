(() => {
  "use strict";

  const variants = Object.freeze(["a", "b", "c", "d", "e"]);
  const assets = Object.freeze({
    a: "/assets/heroes/hero-a-creator.webp",
    b: "/assets/heroes/hero-b-frog.webp",
    c: "/assets/heroes/hero-c-botanical.webp",
    d: "/assets/heroes/hero-d-product.webp",
    e: "/assets/heroes/hero-e-cinematic.webp",
  });
  const cookieName = "pz_hero_variant";
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${cookieName}=([a-e])(?:;|$)`));
  let variant = match ? match[1] : "";

  if (!variants.includes(variant)) {
    const random = new Uint32Array(1);
    if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(random);
    else random[0] = Math.floor(Math.random() * 0x100000000);
    variant = variants[random[0] % variants.length];
  }

  const secure = location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${cookieName}=${variant}; Max-Age=7776000; Path=/; SameSite=Lax${secure}`;
  document.documentElement.dataset.heroVariant = variant;
  globalThis.PhotozhabHeroExperiment = Object.freeze({
    name: "landing_hero",
    variant,
    asset: assets[variant],
  });

  const preload = document.createElement("link");
  preload.rel = "preload";
  preload.as = "image";
  preload.type = "image/webp";
  preload.href = assets[variant];
  preload.fetchPriority = "high";
  document.head.append(preload);
})();
