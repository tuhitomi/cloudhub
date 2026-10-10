// DOM unit tests with mocked services. This is not browser or AWS evidence.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {parseHTML} from 'linkedom';
const html=await readFile(new URL('../frontend/index.html',import.meta.url),'utf8');
const source=await readFile(new URL('../frontend/portal.js',import.meta.url),'utf8');
const {createPortal}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const quota={active:1,max_active:2,remaining_environments:1,user_budget_milliusd:10000,remaining_milliusd:2000};
const token=groups=>'header.'+Buffer.from(JSON.stringify({'cognito:groups':groups,exp:Math.floor(Date.now()/1000)+3600,sub:'alice'})).toString('base64url')+'.signature';
const flush=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
function setup(groups=[]){
  const {window}=parseHTML(html);globalThis.document=window.document;globalThis.window=window;
  for(const select of document.querySelectorAll('select'))Object.defineProperty(select,'value',{writable:true,value:select.querySelector('option')?.getAttribute('value')||''});
  for(const dialog of document.querySelectorAll('dialog')){dialog.showModal=()=>dialog.setAttribute('open','');dialog.close=()=>dialog.removeAttribute('open');}
  for(const form of document.querySelectorAll('form'))form.reset=()=>{for(const field of form.querySelectorAll('input')){field.value='';field.checked=false;}};
  const responses=new Map([['/limits',quota],['/notifications',{items:[]}],['/operations',{items:[]}],['/admin/environments',{items:[]}],['/admin/users',{items:[],next_cursor:null}],['/admin/notifications',{status:'NOT_CONFIGURED',email:''}]]),calls=[];
  const element=(tag,text,cls)=>{const value=document.createElement(tag);if(text!==undefined)value.textContent=text;if(cls)value.className=cls;return value;};
  const button=(text,fn,disabled=false)=>{const value=element('button',text);value.disabled=disabled;value.addEventListener('click',()=>Promise.resolve(fn()).catch(error=>document.querySelector('#notice').textContent=error.message));return value;};
  const api=async path=>{calls.push(path);if(!responses.has(path))throw Error('Unexpected endpoint '+path);return responses.get(path);};
  const context={api,post:api,notice:text=>document.querySelector('#notice').textContent=text,element,button,timestamp:n=>String(n||''),track:()=>{},refresh:async()=>{},operations:new Map(),renderOperations:()=>{},cfg:{configured:true,region:'ap-southeast-1',clientId:'client'},getToken:()=>token(groups),ensureToken:async()=>{},mutate:api};
  return {portal:createPortal(context),context,responses,calls,element,$:selector=>document.querySelector(selector)};
}
test('creation fails closed until limits load and enforces remaining reservation',async()=>{
  const {portal,responses}=setup();assert.equal(portal.costAllowed({approved:true,total_milliusd:100}),false);
  await portal.loadLimits();assert.equal(portal.costAllowed({approved:true,total_milliusd:1999}),true);assert.equal(portal.costAllowed({approved:true,total_milliusd:2001}),false);
  responses.set('/limits',{...quota,remaining_environments:0});await portal.loadLimits();assert.equal(portal.costAllowed({approved:true,total_milliusd:0}),false);
});
test('history shares search, state and template filters and renders IDs as text',()=>{
  const {portal,$}=setup();const env={environment_id:'env-<img src=x>',template_id:'T01',status:'DELETED',retired_at:100};
  portal.renderHistory([env,{...env,environment_id:'env-active',status:'READY'}]);assert.equal($('#environment-history').querySelectorAll('article').length,1);assert.equal($('#environment-history').querySelectorAll('img').length,0);
  $('#environment-search').value='active';portal.renderHistory([env]);assert.equal($('#environment-history').querySelectorAll('article').length,0);
  $('#environment-search').value='';$('#environment-template').value='T02';assert.equal(portal.filter([env]).length,0);
});
test('a recovery point in use disables download and delete, with honest native labels',()=>{
  const {portal,element}=setup();const card=element('article');portal.backupExtras(card,{recovery_point:'rp-id',template_id:'T01',status:'AVAILABLE',in_use:true,retain_until:Date.now()/1000+3600});
  assert.equal(card.querySelector('button').textContent,'Tải thông tin snapshot');assert.ok([...card.querySelectorAll('button')].every(value=>value.disabled));
});
test('a final retirement backup blocks deletion while still allowing download',()=>{
  const {portal,element}=setup();const card=element('article');portal.backupExtras(card,{recovery_point:'rp-id',template_id:'T01',status:'AVAILABLE',retirement_protected:true,retain_until:Date.now()/1000+3600});
  const buttons=card.querySelectorAll('button');assert.equal(buttons[0].disabled,false);assert.equal(buttons[1].disabled,true);assert.match(card.textContent,/được bảo vệ/);
});

test('failed provisioning cannot retry; cancelling and native export have separate controls',()=>{
  const {portal,element}=setup();const card=element('article');portal.operationExtras(card,{operation_id:'op-id',status:'FAILED',phase:'CREATE'});
  const buttons=card.querySelectorAll('button');assert.equal(buttons[0].disabled,true);assert.equal(buttons[1].textContent,'Đóng tác vụ lỗi');
});
test('admin visibility uses exact group and pending SNS confirmation keeps test disabled',async()=>{
  const bad=setup('notadmin');assert.equal(bad.portal.isAdmin(),false);
  const {portal,responses,$}=setup(['admin']);responses.set('/admin/notifications',{status:'PENDING_CONFIRMATION',email:'admin@example.com'});
  await portal.refreshOwn();await portal.loadAdmin();assert.equal($('#admin').hidden,false);assert.equal($('#sns-test').disabled,true);assert.match($('#sns-status').textContent,/Confirm subscription/);
});
test('password change uses Cognito directly and clears passwords on provider error',async()=>{
  const {context,$}=setup();const requests=[];globalThis.fetch=async(url,options)=>{requests.push({url,...options});return {ok:false,json:async()=>({__type:'NotAuthorizedException'})};};
  $('#previous-password').value='old-secret';$('#new-password').value='new-secret';$('#change-password-form').onsubmit({preventDefault(){},currentTarget:$('#change-password-form')});await flush();
  assert.equal(requests.length,1);assert.equal(requests[0].url,'https://cognito-idp.ap-southeast-1.amazonaws.com/');assert.match(requests[0].headers['X-Amz-Target'],/ChangePassword$/);
  assert.equal($('#previous-password').value,'');assert.equal($('#new-password').value,'');assert.match($('#account-notice').textContent,/không hợp lệ/);assert.equal(context.operations.size,0);
});
test('forgot/reset uses confirmation code and clears new password after success',async()=>{
  const {$}=setup();const requests=[];globalThis.fetch=async(url,options)=>{requests.push(JSON.parse(options.body));return {ok:true,json:async()=>({})};};
  $('#forgot-email').value='student@example.com';$('#reset-code').value='123456';$('#reset-password').value='NewPassword123!';$('#reset-form').onsubmit({preventDefault(){},currentTarget:$('#reset-form')});await flush();
  assert.equal(requests[0].ConfirmationCode,'123456');assert.equal(requests[0].Username,'student@example.com');assert.equal($('#reset-password').value,'');assert.match($('#password-notice').textContent,/Đã đặt mật khẩu/);
});
test('complete app starts with real HTML and mocked owned API, with no browser automation',async()=>{
  const {context,$}=setup();window.CLOUDHUB=context.cfg;const storage=new Map([['cloudhub_access_token',token([])]]);globalThis.sessionStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key),clear:()=>storage.clear()};
  globalThis.location={search:'',pathname:'/',assign(){}};globalThis.history={replaceState(){}};
  const values={'/templates':{items:[]},'/environments':{items:[]},'/recovery-points':{items:[]},'/limits':quota,'/notifications':{items:[]},'/operations':{items:[]}};
  globalThis.fetch=async url=>{const path=new URL(url).pathname;assert.ok(values[path],'unexpected '+path);return {ok:true,json:async()=>values[path]};};window.CLOUDHUB.apiBase='https://api.example';
  const timer=globalThis.setInterval;globalThis.setInterval=()=>1;
  try{const app=(await readFile(new URL('../frontend/app.js',import.meta.url),'utf8')).replace("'./portal.js'",JSON.stringify('data:text/javascript;base64,'+Buffer.from(source).toString('base64')));await import('data:text/javascript;base64,'+Buffer.from(app).toString('base64'));await flush();assert.match($('#limits-summary').textContent,/Còn 1 môi trường/);assert.equal($('#logout').hidden,false);assert.equal($('#admin').hidden,true);assert.match($('#notice').textContent,/Đã đăng nhập/);}finally{globalThis.setInterval=timer;}
});
