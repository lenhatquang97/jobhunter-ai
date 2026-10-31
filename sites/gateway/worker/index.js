// Odoo owns authentication and business permissions; this is a fixed origin gateway.
function unavailable(status) {
  return new Response(status === 503 ? 'The workspace is starting. Please try again shortly.' : 'The workspace is temporarily unavailable.', {
    status, headers: {'content-type':'text/plain; charset=utf-8','cache-control':'private, no-store'},
  });
}

export default {
  async fetch(request, env) {
    let upstream, canonical;
    try {
      upstream = new URL(env.UPSTREAM_ORIGIN);
      canonical = new URL(env.PUBLIC_ORIGIN);
      if (upstream.protocol !== 'https:' || !/^[a-z0-9]+\.cloudfront\.net$/.test(upstream.hostname)
        || upstream.port || upstream.username || upstream.password || upstream.pathname !== '/'
        || upstream.search || upstream.hash || canonical.protocol !== 'https:'
        || canonical.username || canonical.password || canonical.pathname !== '/'
        || canonical.search || canonical.hash || !env.ORIGIN_TOKEN) return unavailable(503);
    } catch { return unavailable(503); }

    const incoming = new URL(request.url);
    let path;
    try { path = decodeURIComponent(incoming.pathname).toLowerCase(); }
    catch { return new Response('Invalid path', {status:400}); }
    if (/^\/web\/(database|tests|qunit)(\/|$)/.test(path)
      || /^\/(xmlrpc|jsonrpc)(\/|$)/.test(path)
      || /^\/web\/assets\/debug(\/|$)/.test(path)
      || incoming.searchParams.has('debug')) {
      return new Response('This route is disabled.', {status:403,headers:{'cache-control':'private, no-store'}});
    }
    if (incoming.pathname === '/') {
      return new Response(null, {status:302,headers:{location:'/odoo/hr.applicant','cache-control':'private, no-store'}});
    }

    const destination = new URL(upstream);
    destination.pathname = incoming.pathname;
    destination.search = incoming.search;
    if (destination.origin !== upstream.origin) return unavailable(503);
    const headers = new Headers(request.headers);
    for (const key of [...headers.keys()]) {
      if (/^(host|forwarded|x-forwarded-.*|x-real-ip|cf-.*|oai-.*|x-jobhunter-origin-token)$/i.test(key)) headers.delete(key);
    }
    headers.set('x-jobhunter-origin-token', env.ORIGIN_TOKEN);
    headers.set('x-forwarded-host', canonical.host);
    headers.set('x-forwarded-proto', 'https');
    headers.set('x-forwarded-port', '443');
    const options = {method:request.method,headers,redirect:'manual'};
    if (!['GET','HEAD'].includes(request.method)) { options.body=request.body; options.duplex='half'; }

    let response;
    try {
      response = await fetch(new Request(destination, options), {cf:{cacheTtl:0,cacheEverything:false}});
    } catch { return unavailable(502); }
    // Upgrade responses own their socket; recreating one would lose it.
    if (response.status === 101) return response;
    const outgoing = new Headers(response.headers);
    outgoing.set('cache-control','private, no-store');
    outgoing.delete('cdn-cache-control');
    outgoing.delete('cloudflare-cdn-cache-control');
    for (const key of ['location','content-location']) {
      const value = outgoing.get(key);
      if (!value) continue;
      try {
        const target = new URL(value, canonical);
        if (target.origin === upstream.origin) outgoing.set(key, canonical.origin + target.pathname + target.search + target.hash);
      } catch { outgoing.delete(key); }
    }
    const cookies = response.headers.getSetCookie();
    outgoing.delete('set-cookie');
    for (let cookie of cookies) {
      cookie = cookie.replace(/;\s*Domain=[^;]+/ig,'');
      if (!/;\s*Secure(?:;|$)/i.test(cookie)) cookie += '; Secure';
      outgoing.append('set-cookie',cookie);
    }
    return new Response(response.body,{status:response.status,statusText:response.statusText,headers:outgoing});
  },
};
