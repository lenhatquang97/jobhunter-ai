const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');
const token = 'synthetic-test-token-only';
const source = fs.readFileSync('infra/terraform/origin-gate.js.tftpl','utf8').replace('${token_hash}',crypto.createHash('sha256').update(token).digest('hex'));
const context={require};vm.createContext(context);vm.runInContext(source,context);
function run(uri, supplied=token, querystring={}) {
 const headers=supplied===null?{}:{'x-jobhunter-origin-token':{value:supplied}};
 return context.handler({request:{uri,headers,querystring,method:'POST',body:{data:'unchanged'},cookies:{session_id:{value:'synthetic'}}}});
}
test('requires the exact private origin token',()=>{
 assert.equal(run('/web/login',null).statusCode,403);
 assert.equal(run('/web/login','wrong').statusCode,403);
});
test('strips token and preserves native POST/cookie/body',()=>{
 const result=run('/jobhunter/chat/rpc');
 assert.equal(result.headers['x-jobhunter-origin-token'],undefined);
 assert.equal(result.method,'POST');assert.equal(result.body.data,'unchanged');
 assert.equal(result.cookies.session_id.value,'synthetic');
});
test('blocks management and developer endpoints after authentication',()=>{
 for(const uri of ['/web/database/manager','/WEB/DATABASE','/web/tests','/web/assets/debug/test','/xmlrpc/2/db','/jsonrpc']) assert.equal(run(uri).statusCode,404,uri);
 assert.equal(run('/odoo',token,{debug:{value:'assets'}}).statusCode,404);
});
