/**
 * sw.js — Service Worker PaieGabon PWA
 * Stratégie :
 *   • Assets statiques  → cache-first
 *   • Pages (navigation)→ network-first SANS mise en cache des pages authentifiées
 *     (données de paie : jamais de page périmée), avec repli hors-ligne.
 *
 * Correctifs v4 :
 *   - Ne casse plus la navigation quand la réponse est une redirection
 *     (ex. /dashboard → /login) : la réponse est reconstruite pour retirer le
 *     drapeau « redirected », que le navigateur refuse pour une navigation.
 *   - Ne met plus en cache les pages authentifiées (fini les pages figées/périmées).
 *   - Repli d'asset hors-ligne corrigé (ne renvoie plus « undefined »).
 *   - Bump de version → purge automatique des anciens caches (débloque les PWA
 *     déjà installées avec une page cassée en cache).
 */

const CACHE_ASSETS = 'paiegabon-assets-v4';
const OFFLINE_PAGE = '/offline';

// Assets à précharger (tolérant aux échecs réseau)
const PRECACHE_ASSETS = [
  OFFLINE_PAGE,
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

// ── Installation ──────────────────────────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_ASSETS)
      .then(cache => Promise.allSettled(PRECACHE_ASSETS.map(u => cache.add(u))))
      .catch(() => {})
  );
  self.skipWaiting();
});

// ── Activation : purge de TOUS les anciens caches ─────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE_ASSETS).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// ── Interception des requêtes ─────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // On ne gère que le GET ; jamais l'API, l'admin ou la déconnexion.
  if (request.method !== 'GET') return;
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/admin') ||
      url.pathname.includes('logout')) return;

  const isHTML = request.mode === 'navigate' ||
                 (request.headers.get('accept') || '').includes('text/html');

  // ── Assets statiques → cache-first ──────────────────────────────────────────
  if (!isHTML && (url.pathname.startsWith('/static/') ||
                  url.hostname === 'cdn.tailwindcss.com' ||
                  url.hostname === 'cdn.jsdelivr.net')) {
    event.respondWith(
      caches.match(request).then(cached => {
        if (cached) return cached;
        return fetch(request).then(response => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_ASSETS).then(c => c.put(request, clone)).catch(() => {});
          }
          return response;
        }).catch(() => cached || Response.error());
      })
    );
    return;
  }

  // ── Pages (navigation) → network-first, sans cache, repli hors-ligne ────────
  if (isHTML) {
    event.respondWith((async () => {
      try {
        const net = await fetch(request);
        // Une navigation ne peut pas recevoir une réponse « redirigée » via le SW :
        // on la reconstruit à l'identique pour retirer ce drapeau (sinon page figée).
        if (net.redirected) {
          const body = await net.clone().blob();
          return new Response(body, {
            status: net.status, statusText: net.statusText, headers: net.headers,
          });
        }
        return net;
      } catch (e) {
        // Hors connexion : page de secours (jamais une page authentifiée périmée).
        const offline = await caches.match(OFFLINE_PAGE);
        return offline || new Response(
          '<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">' +
          '<meta name="viewport" content="width=device-width,initial-scale=1">' +
          '<title>Hors connexion — PaieGabon</title>' +
          '<style>body{font-family:system-ui;background:#0f172a;color:#fff;display:flex;' +
          'align-items:center;justify-content:center;min-height:100vh;margin:0;text-align:center;padding:2rem}' +
          '.card{background:#1e293b;border-radius:1.25rem;padding:2rem;max-width:360px}' +
          'a{background:#0f3d36;color:#fff;padding:.75rem 1.5rem;border-radius:.875rem;' +
          'text-decoration:none;font-weight:700;display:inline-block;margin-top:1rem}</style></head>' +
          '<body><div class="card"><div style="font-size:3rem">📶</div>' +
          '<h1 style="font-size:1.25rem;margin:.5rem 0">Hors connexion</h1>' +
          '<p style="color:#94a3b8;font-size:.9rem">Vérifiez votre connexion Internet, puis réessayez.</p>' +
          '<a href="/dashboard">Réessayer</a></div></body></html>',
          { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
        );
      }
    })());
    return;
  }
});

// ── Message : permettre au client de forcer l'activation ──────────────────────
self.addEventListener('message', event => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});
