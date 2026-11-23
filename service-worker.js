/* Verox Android web-push service worker. Never put secrets in this file. */
self.addEventListener("push", event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) {}
  event.waitUntil(self.registration.showNotification(
    String(data.title || "Verox — Validation humaine requise").slice(0, 120),
    { body: String(data.body || "Une demande attend votre décision dans Verox.").slice(0, 220),
      tag: String(data.request_id || "verox-approval").slice(0, 120), renotify: true,
      data: { url: String(data.url || "/approvals.html").slice(0, 500) } }
  ));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || "/approvals.html", self.location.origin).href;
  event.waitUntil((async () => {
    const windows = await clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of windows) {
      if (new URL(client.url).origin === self.location.origin && "focus" in client) {
        await client.navigate(target); return client.focus();
      }
    }
    return clients.openWindow(target);
  })());
});
