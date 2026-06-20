const CACHE_NAME = 'mekalmart-v2';
const ASSETS = [
  'index.html',
  'config.js',
  'style.css',
  'script.js',
  'vendor_dashboard.html',
  'delivery_dashboard.html',
  'admin_dashboard.html'
];

// Install Event
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS);
    })
  );
});

// Activate Event
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
});

// Fetch Event
self.addEventListener('fetch', (event) => {
  // Ignore non-GET requests and API requests
  const url = event.request.url;
  if (
    event.request.method !== 'GET' || 
    url.includes('/auth/') || 
    url.includes('/products') || 
    url.includes('/shops') || 
    url.includes('/orders') || 
    url.includes('/vendor/') || 
    url.includes('/delivery/') || 
    url.includes('/notifications') || 
    url.includes('/wallet/') || 
    url.includes('/ai/')
  ) {
    event.respondWith(fetch(event.request));
    return;
  }

  event.respondWith(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.match(event.request).then((cachedResponse) => {
        const fetchedResponse = fetch(event.request).then((networkResponse) => {
          if (networkResponse.status === 200) {
            cache.put(event.request, networkResponse.clone());
          }
          return networkResponse;
        }).catch(() => {
          // Ignore network errors when offline
        });
        return cachedResponse || fetchedResponse;
      });
    })
  );
});

// Push Notification Event
self.addEventListener('push', (event) => {
  let data = { title: 'New Order!', body: 'Check your Mekal Mart dashboard.' };
  if (event.data) {
    data = event.data.json();
  }

  const options = {
    body: data.body,
    icon: 'https://cdn-icons-png.flaticon.com/512/3514/3514491.png',
    badge: 'https://cdn-icons-png.flaticon.com/512/3514/3514491.png',
    vibrate: [100, 50, 100],
    data: {
      url: self.registration.scope
    }
  };

  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

// Notification Click Event
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    clients.openWindow(event.notification.data.url)
  );
});
