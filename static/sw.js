/**
 * sw.js — Service Worker PaieGabon PWA
 * Stratégie : Cache-first pour assets statiques, Network-first pour les pages
 */

const CACHE_NAME    = 'paiegalon-v2';
const CACHE_ASSETS  = 'paiegalon-assets-v2';

// Assets statiques à mettre en cache immédiatement
const PRECACHE_ASSETS = [
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  'https://cdn.tailwindcss.com',
];

// Pages à mettre en cache pour usage offline (après première visite)
const CACHE_PAGES = [
  '/dashboard',
  '/pointage/individuel',
  '/journaliers',
  '/salaries',
];

// Page offline de secours
const OFFLINE_PAGE = '/offline';

// ── Installation ──────────────────────────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_ASSETS).then(cache => {
      return cache.addAll(PRECACHE_ASSETS.map(url => new Request(url, { mode: 'no-cors' })));
    }).catch(() => {})
  );
  self.skipWaiting();
});

// ── Activation (nettoyage des anciens caches) ─────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME && k !== CACHE_ASSETS)
            .map(k => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// ── Interception des requêtes ─────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Ignorer les requêtes non-GET, API, admin
  if (request.method !== 'GET') return;
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/admin') ||
      url.pathname.includes('logout')) return;

  // Assets statiques → Cache-first
  if (url.pathname.startsWith('/static/') ||
      url.hostname === 'cdn.tailwindcss.com' ||
      url.hostname === 'cdn.jsdelivr.net') {
    event.respondWith(
      caches.match(request).then(cached => {
        if (cached) return cached;
        return fetch(request).then(response => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_ASSETS).then(cache => cache.put(request, clone));
          }
          return response;
        }).catch(() => cached);
      })
    );
    return;
  }

  // Pages HTML → Network-first avec fallback cache
  if (request.headers.get('accept')?.includes('text/html')) {
    event.respondWith(
      fetch(request)
        .then(response => {
          // Mettre en cache si succès
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(request, clone));
          }
          return response;
        })
        .catch(async () => {
          // Offline → chercher dans le cache
          const cached = await caches.match(request);
          if (cached) return cached;
          // Fallback sur la page offline
          const offline = await caches.match(OFFLINE_PAGE);
          return offline || new Response(
            `<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
            <meta name="viewport" content="width=device-width,initial-scale=1">
            <title>Hors connexion — PaieGabon</title>
            <style>body{font-family:system-ui;background:#0f172a;color:white;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;flex-direction:column;gap:1rem;text-align:center;padding:2rem}
            .card{background:#1e293b;border-radius:1.25rem;padding:2rem;max-width:360px;width:100%}
            a{background:#6d28d9;color:white;padding:.75rem 1.5rem;border-radius:.875rem;text-decoration:none;font-weight:700;display:inline-block}</style></head>
            <body><div class="card">
            <div style="font-size:3rem;margin-bottom:.75rem">📶</div>
            <h1 style="font-size:1.25rem;font-weight:900;margin:0 0 .5rem">Hors connexion</h1>
            <p style="color:#94a3b8;font-size:.875rem;margin:0 0 1.5rem">Vous n'êtes pas connecté à Internet. Les pages visitées récemment sont disponibles.</p>
            <a href="/dashboard">← Retour au tableau de bord</a>
            </div></body></html>`,
            { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
          );
        })
    );
    return;
  }
});

// ── Sync en arrière-plan (pour les pointages offline) ────────────────────────
self.addEventListener('sync', event => {
  if (event.tag === 'sync-pointages') {
    event.waitUntil(syncPointagesOffline());
  }
});

async function syncPointagesOffline() {
  // Récupérer les pointages en attente depuis IndexedDB (si implémenté)
  // Pour l'instant : log simple
  console.log('[SW] Sync pointages offline');
}

// ── Messages depuis le client ─────────────────────────────────────────────────
self.addEventListener('message', event => {
  if (event.data === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  if (event.data === 'CACHE_PAGE') {
    // Le client demande à mettre en cache la page actuelle
    self.clients.matchAll().then(clients => {
      clients.forEach(client => client.postMessage('PAGE_CACHED'));
    });
  }
});
