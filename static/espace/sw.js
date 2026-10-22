/* PaieGabon — Mon espace salarié : service worker.
   Volontairement minimal : les pages et les bulletins (données personnelles)
   ne sont JAMAIS mis en cache, pour qu'un téléphone partagé ou perdu ne
   garde rien. Seule la page « hors ligne » est conservée. */
const CACHE = "espace-v1";
const HORS_LIGNE = "/espace/hors-ligne";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll([HORS_LIGNE, "/static/icons/icon-192.png"])));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((cles) =>
    Promise.all(cles.filter((k) => k.startsWith("espace-") && k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return;           // tout le reste : réseau normal
  e.respondWith(fetch(e.request).catch(() => caches.match(HORS_LIGNE)));
});
