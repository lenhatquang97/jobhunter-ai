import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import worker from '../worker/index.js';

const nativeFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = nativeFetch; });
const env = {
  UPSTREAM_ORIGIN: 'https://d123.cloudfront.net',
  PUBLIC_ORIGIN: 'https://jobhunter-workspace.netlink269.chatgpt.site',
  ORIGIN_TOKEN: 'synthetic-private-origin-token',
};
const request = (path, options) => new Request(env.PUBLIC_ORIGIN + path, options);

test('root opens native recruitment without an origin request', async () => {
  globalThis.fetch = () => { throw new Error('unexpected upstream'); };
  const response = await worker.fetch(request('/'), env);
  assert.equal(response.status, 302);
  assert.equal(response.headers.get('location'), '/odoo/hr.applicant');
});

test('forwards a CSRF-protected POST once with canonical identity and cookies', async () => {
  let calls = 0;
  globalThis.fetch = async (upstream) => {
    calls++;
    assert.equal(upstream.url, env.UPSTREAM_ORIGIN + '/web/login?redirect=%2Fodoo');
    assert.equal(upstream.method, 'POST');
    assert.equal(await upstream.text(), 'login=synthetic&csrf_token=test');
    assert.equal(upstream.headers.get('cookie'), 'session_id=synthetic');
    assert.equal(upstream.headers.get('x-jobhunter-origin-token'), env.ORIGIN_TOKEN);
    assert.equal(upstream.headers.get('x-forwarded-host'), new URL(env.PUBLIC_ORIGIN).host);
    assert.equal(upstream.headers.get('x-forwarded-proto'), 'https');
    assert.equal(upstream.headers.get('forwarded'), null);
    assert.equal(upstream.headers.get('x-forwarded-for'), null);
    assert.equal(upstream.headers.get('oai-authenticated-user-email'), null);
    assert.equal(upstream.redirect, 'manual');
    return new Response('csrf checked', {status: 200});
  };
  const response = await worker.fetch(request('/web/login?redirect=%2Fodoo', {
    method: 'POST', body: 'login=synthetic&csrf_token=test',
    headers: {'cookie':'session_id=synthetic', 'forwarded':'host=evil',
      'x-forwarded-host':'evil', 'x-forwarded-for':'127.0.0.1',
      'oai-authenticated-user-email':'forged@example.invalid'},
  }), env);
  assert.equal(await response.text(), 'csrf checked');
  assert.equal(calls, 1);
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
});

test('rewrites upstream redirects and preserves separate secure cookies', async () => {
  const headers = new Headers({location: env.UPSTREAM_ORIGIN + '/odoo'});
  headers.append('set-cookie', 'session_id=abc; Domain=d123.cloudfront.net; Path=/; HttpOnly; SameSite=Lax');
  headers.append('set-cookie', 'cids=1; Path=/; SameSite=Lax');
  globalThis.fetch = async () => new Response(null, {status:303,headers});
  const response = await worker.fetch(request('/web/login'), env);
  assert.equal(response.headers.get('location'), env.PUBLIC_ORIGIN + '/odoo');
  assert.equal(response.headers.getSetCookie().length, 2);
  for (const cookie of response.headers.getSetCookie()) {
    assert.doesNotMatch(cookie, /domain=/i);
    assert.match(cookie, /; Secure/i);
  }
});

test('blocks management and development paths before reaching the origin', async () => {
  globalThis.fetch = () => { throw new Error('unexpected upstream'); };
  for (const path of ['/web/database/manager','/web/%64atabase/backup',
    '/web/tests','/web/assets/debug/test.js','/odoo?debug=assets']) {
    assert.equal((await worker.fetch(request(path), env)).status, 403, path);
  }
});

test('fails closed for missing credentials or invalid origin configuration', async () => {
  globalThis.fetch = () => { throw new Error('unexpected upstream'); };
  for (const config of [{...env,ORIGIN_TOKEN:''}, {...env,UPSTREAM_ORIGIN:'https://evil.example'},
    {...env,UPSTREAM_ORIGIN:env.UPSTREAM_ORIGIN+'/other'}]) {
    assert.equal((await worker.fetch(request('/odoo'), config)).status, 503);
  }
});

test('streams response and returns websocket upgrade unchanged', async () => {
  const upgrade = {status:101, webSocket:{synthetic:true}};
  globalThis.fetch = async upstream => {
    assert.equal(upstream.headers.get('upgrade'), 'websocket');
    return upgrade;
  };
  assert.equal(await worker.fetch(request('/websocket', {headers:{upgrade:'websocket'}}), env), upgrade);
  const stream = new ReadableStream({start(controller) {controller.enqueue(new TextEncoder().encode('one'));controller.close();}});
  globalThis.fetch = async () => new Response(stream);
  const response = await worker.fetch(request('/web/assets/app.js'), env);
  assert.equal(await response.text(), 'one');
});

test('upstream errors have no private origin details and are never replayed', async () => {
  let calls=0;
  globalThis.fetch = async () => {calls++; throw new Error(env.ORIGIN_TOKEN);};
  const response = await worker.fetch(request('/jobhunter/chat', {method:'POST',body:'{}'}), env);
  assert.equal(response.status, 502);
  assert.doesNotMatch(await response.text(), /synthetic-private|cloudfront/);
  assert.equal(calls,1);
});

test('double-slash and encoded separators never change the fixed upstream origin', async () => {
  let calls=0;
  globalThis.fetch = async upstream => {
    calls++;
    assert.equal(new URL(upstream.url).origin, 'https://d123.cloudfront.net');
    return new Response('fixed');
  };
  for (const path of ['//attacker.example/path', '/%2f%2fattacker.example/path',
    '/%5c%5cattacker.example/path']) {
    assert.equal((await worker.fetch(request(path),env)).status,200);
  }
  assert.equal(calls,3);
});
