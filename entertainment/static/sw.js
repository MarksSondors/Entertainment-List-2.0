// Service Worker for Entertainment List PWA
const CACHE_NAME = 'entertainment-list-v1';
const urlsToCache = [
  '/',
  '/static/css/',
  '/static/js/',
  '/static/favicon/web-app-manifest-192x192.png',
  '/static/favicon/web-app-manifest-512x512.png',
  '/static/favicon/apple-touch-icon.png',
  '/static/favicon/favicon-96x96.png'
];

// Install Service Worker
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        console.log('Opened cache');
        return cache.addAll(urlsToCache.map(url => {
          return new Request(url, {cache: 'reload'});
        }));
      })
      .catch((error) => {
        console.log('Cache addAll failed:', error);
      })
  );
});

// Fetch event
self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request)
      .then((response) => {
        // Return cached version or fetch from network
        return response || fetch(event.request);
      })
  );
});

// Activate event
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});

// Push Notification Event Handler
self.addEventListener('push', (event) => {
  console.log('Push notification received:', event);
  
  let notificationData = {
    title: 'Entertainment List',
    body: 'You have a new notification',
    icon: '/static/favicon/web-app-manifest-192x192.png',
    badge: '/static/favicon/favicon-96x96.png',
    vibrate: [200, 100, 200],
    tag: 'default',
    requireInteraction: false,
    silent: false,
  };

  // Parse notification data if available
  if (event.data) {
    try {
      const data = event.data.json();
      notificationData = {
        title: data.title || notificationData.title,
        body: data.body || notificationData.body,
        icon: data.icon || notificationData.icon,
        badge: data.badge || notificationData.badge,
        vibrate: data.vibrate || notificationData.vibrate,
        tag: data.tag || notificationData.tag,
        requireInteraction: data.requireInteraction || false,
        silent: data.silent || false,
        data: data.url ? { url: data.url } : {},
        // iOS has limited support for action buttons, include them but they may not display
        actions: data.actions || [],
      };
    } catch (e) {
      console.error('Error parsing push notification data:', e);
      notificationData.body = event.data.text();
    }
  }

  event.waitUntil(
    self.registration.showNotification(notificationData.title, notificationData)
  );
});

// Notification Click Event Handler
self.addEventListener('notificationclick', (event) => {
  console.log('Notification clicked:', event);
  
  event.notification.close();

  // Handle action button clicks
  if (event.action) {
    console.log('Action clicked:', event.action);
    // Handle specific actions if defined
  }

  // Navigate to URL if provided
  const urlToOpen = event.notification.data?.url || '/';
  
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then((clientList) => {
        // Check if there's already a window open
        for (let i = 0; i < clientList.length; i++) {
          const client = clientList[i];
          if (client.url === urlToOpen && 'focus' in client) {
            return client.focus();
          }
        }
        // If no window is open, open a new one
        if (clients.openWindow) {
          return clients.openWindow(urlToOpen);
        }
      })
  );
});

// Notification Close Event Handler (optional analytics)
self.addEventListener('notificationclose', (event) => {
  console.log('Notification closed:', event);
  // You can track notification dismissals here
});
