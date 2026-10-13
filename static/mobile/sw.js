/* Service worker — PWA pointage chantier PaieGabon.
   Rôle : rendre l'app disponible hors-ligne (cache du shell) et réveiller la
   synchronisation. Toute la logique de file d'attente/sync vit dans la page
   (IndexedDB), le SW ne fait que servir le shell et notifier la page. */
const CACHE = "pg-pointage-v5";
const SHELL = [
  "/m",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/m/manifest.webmanifest",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Les appels API ne sont jamais servis depuis le cache : la page gère
  // elle-même le hors-ligne via sa file IndexedDB.
  if (url.pathname.startsWith("/m/api/")) return;

  // Navigation vers la coque : réseau d'abord, repli sur le cache hors-ligne.
  if (e.request.mode === "navigate" || url.pathname === "/m" || url.pathname === "/m/") {
    e.respondWith(
      fetch(e.request).then((r) => {
        const copy = r.clone();
        caches.open(CACHE).then((c) => c.put("/m", copy));
        return r;
      }).catch(() => caches.match("/m"))
    );
    return;
  }

  // Autres ressources (icônes, etc.) : cache d'abord.
  e.respondWith(caches.match(e.request).then((c) => c || fetch(e.request)));
});

// Background Sync : réveille la page pour qu'elle vide sa file.
self.addEventListener("sync", (e) => {
  if (e.tag === "sync-pointages") {
    e.waitUntil(
      self.clients.matchAll({ includeUncontrolled: true }).then((cs) =>
        cs.forEach((c) => c.postMessage({ type: "sync-pointages" }))
      )
    );
  }
});
