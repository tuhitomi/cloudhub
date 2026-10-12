import {createPortal} from './portal.js';
import {createCognitoAuth,refreshCognito,cognitoRequest} from './auth.js';
const $ = s => document.querySelector(s);
const cfg = window.CLOUDHUB || {};
let accessToken = sessionStorage.getItem('cloudhub_access_token');
let refreshToken=sessionStorage.getItem('cloudhub_refresh_token'),refreshPromise;
let selected, recoveryPoint, latestQuote, busy = false, quoteSerial = 0;
const commands = new Map(), operations = new Map();
let fileEnvironment, recordEnvironment;
let cachedEnvironments=[];
const catalogCache=new Map();
const stateNames = {READY:'Sẵn sàng',PROVISIONING:'Đang triển khai',BACKING_UP:'Đang sao lưu',RETIRING:'Đang thu hồi',DELETED:'Đã thu hồi/xóa',DELETING:'Đang xóa bản sao',DELETE_FAILED:'Xóa chưa hoàn tất',RESIDUAL:'Còn tài nguyên cần xử lý',ERROR_RETAINED:'Có lỗi · Đang giữ dữ liệu',CREATE_FAILED:'Triển khai thất bại',AVAILABLE:'Có thể khôi phục',PENDING:'Đang xử lý',FAILED:'Thất bại',EXPIRED:'Đã hết hạn',EXPIRING:'Đang xóa theo chính sách'};
function element(tag, text, className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function notice(text){$('#notice').textContent=text;}
function timestamp(sec){return sec?new Date(Number(sec)*1000).toLocaleString('vi-VN',{timeZone:'Asia/Bangkok'}):'Chưa bắt đầu';}
function button(text,fn,disabled=false){const e=element('button',text);e.disabled=disabled;e.addEventListener('click',()=>Promise.resolve(fn()).catch(showError));return e;}
function showError(err){notice(err.message || String(err));}
function idempotency(scope){if(!commands.has(scope))commands.set(scope,crypto.randomUUID());return commands.get(scope);}
async function api(path,options={}){if(!cfg.configured)throw Error('Chưa có cấu hình AWS. Chạy hướng dẫn triển khai sau khi được duyệt.');if(!accessToken)throw Error('Hãy đăng nhập để tiếp tục.');await ensureToken();const res=await fetch(cfg.apiBase+path,{...options,headers:{'Authorization':'Bearer '+accessToken,'Content-Type':'application/json',...options.headers}});const data=await res.json();if(!res.ok){if(res.status===401){accessToken=null;sessionStorage.removeItem('cloudhub_access_token');sessionStorage.removeItem('cloudhub_refresh_token');refreshToken=null;}throw Error(data.error?.message||'Lỗi API '+res.status);}return data;}
function post(path,body={},key){return api(path,{method:'POST',body:JSON.stringify(body),headers:key?{'Idempotency-Key':key}:{}});}
function b64(bytes){return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=/g,'');}
async function login(){authentication.open();}
async function callback(){const query=new URLSearchParams(location.search);if(query.has('error'))throw Error('Đăng nhập thất bại: '+query.get('error'));if(!query.has('code'))return;const pkce=JSON.parse(sessionStorage.getItem('pkce')||'null');if(!pkce||query.get('state')!==pkce.state)throw Error('Phiên đăng nhập không hợp lệ.');const r=await fetch(cfg.cognitoDomain+'/oauth2/token',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({grant_type:'authorization_code',client_id:cfg.clientId,code:query.get('code'),redirect_uri:cfg.redirectUri,code_verifier:pkce.verifier})});if(!r.ok)throw Error('Không đổi được mã đăng nhập.');const data=await r.json();accessToken=data.access_token;refreshToken=data.refresh_token||null;sessionStorage.setItem('cloudhub_access_token',accessToken);if(refreshToken)sessionStorage.setItem('cloudhub_refresh_token',refreshToken);sessionStorage.removeItem('pkce');history.replaceState({},'',location.pathname);}
async function logout(){const remembered=refreshToken,method=sessionStorage.getItem('cloudhub_auth_method');sessionStorage.clear();accessToken=null;refreshToken=null;if(method==='api'){try{if(remembered)await cognitoRequest(cfg,'RevokeToken',{ClientId:cfg.clientId,Token:remembered});}catch{/* Local logout still clears the session on network failure. */}location.assign(cfg.redirectUri);return;}const u=new URL(cfg.cognitoDomain+'/logout');u.search=new URLSearchParams({client_id:cfg.clientId,logout_uri:cfg.redirectUri});location.assign(u);}
async function loadCatalog(){const {items}=await api('/templates');$('#catalog').replaceChildren();for(const t of items){catalogCache.set(t.template_id,t);const card=element('article',undefined,'card');card.append(element('span',t.template_id,'tag'),element('h3',t.name),element('p',t.purpose),element('small',t.services.join(' · ')));card.append(element('p',t.create_enabled?'Đã kiểm chứng AWS':t.status==='NOT_IMPLEMENTED'?'Chưa triển khai':'Có mã · Chưa kiểm chứng AWS','badge'));card.append(button('Chọn mẫu',()=>openCreate(t),!t.create_enabled));$('#catalog').append(card);}}
const descriptions={
T01:'Máy Linux nhỏ · root 16 GiB · ổ dữ liệu 8 GiB',T02:'Website tĩnh · 32 file, mỗi file 8 KiB',
T03:'Web và PostgreSQL · 32 bản ghi minh họa, mỗi bản ghi 256 byte',T04:'Web có bộ cân bằng tải · từ 1 đến 2 máy · dùng chung PostgreSQL',
T05:'Website trên Linux · nội dung trong ổ dữ liệu 8 GiB',T06:'Ứng dụng Docker đã duyệt · 1 tác vụ nhỏ · dữ liệu bền vững trong S3',
T07:'Kho file riêng tư · 32 file, mỗi file 8 KiB · liên kết tải tối đa 15 phút',T08:'Hai máy Linux dùng chung EFS · 32 bản ghi nhỏ',
T09:'API và dữ liệu serverless · 32 bản ghi · đọc bằng phiên đăng nhập của bạn',T10:'Kho phân tích riêng tư · nạp một dòng CSV minh họa từ S3 · truy vấn có giới hạn'};
async function openCreate(t,rp){selected=catalogCache.get(t.template_id)||t;recoveryPoint=rp;$('#create-title').textContent=rp?'Khôi phục sang môi trường mới':'Tạo '+selected.name;$('#confirm-cost').checked=false;$('#configuration-description').textContent=descriptions[t.template_id];$('#backup-description').textContent=(selected.backup_scope||'Xem phạm vi bản sao trước khi khôi phục.')+' · Giữ bản sao 7 ngày.';$('#create-dialog').showModal();await updateQuote();}

async function updateQuote(){const serial=++quoteSerial;latestQuote=null;$('#confirm').disabled=true;const [data]=await Promise.all([post('/estimates',{template_id:selected.template_id,duration_hours:Number($('#hours').value)}),portal.loadLimits()]);if(serial!==quoteSerial)return;latestQuote=data;$('#quote').replaceChildren(element('strong',data.total_milliusd==null?'Chưa có đơn giá được duyệt':`Dự toán: ${(Number(data.total_milliusd)/1000).toFixed(3)} USD`),element('small',`Giá cập nhật: ${data.price_updated_at||'Chưa xác nhận'} · Region: ${data.region||'Chưa xác nhận'}`),element('p',(data.unknown||[]).join(' ')));const details=element('details');details.append(element('summary','Đơn giá và giả định'));details.append(element('pre',JSON.stringify({units:data.units,assumptions:data.assumptions,lines:data.lines},null,2)));$('#quote').append(details);$('#confirm').disabled=!portal.costAllowed(data);}
async function submitCreate(ev){ev.preventDefault();if(busy||!portal.costAllowed(latestQuote))return;busy=true;$('#confirm').disabled=true;const body={template_id:selected.template_id,duration_hours:Number($('#hours').value)};if(recoveryPoint)body.recovery_point=recoveryPoint;const scope=JSON.stringify(body);try{const op=await post('/environments',body,idempotency(scope));commands.delete(scope);track(op);$('#create-dialog').close();await refresh();}catch(e){showError(e);}finally{busy=false;$('#confirm').disabled=!portal.costAllowed(latestQuote);}}
function track(op){operations.set(op.operation_id,op);renderOperations();notice('Đã nhận tác vụ '+op.operation_id+'. Có thể theo dõi trạng thái bên dưới.');}
async function downloadKey(eid){const key=await api('/environments/'+eid+'/ssh-key');const url=URL.createObjectURL(new Blob([key.private_key],{type:'application/x-pem-file'}));const link=element('a');link.href=url;link.download=key.filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notice('Đã tải khóa riêng. Giữ file trên máy của bạn, không chia sẻ. Kết nối: '+key.command+' . Trên Linux/macOS dùng chmod 600 cho file khóa.');}
function renderOperations(){$('#operations').replaceChildren();for(const op of [...operations.values()].sort((a,b)=>Number(b.created_at||0)-Number(a.created_at||0))){const card=element('article',undefined,'card');card.append(element('strong',`${op.action} · ${op.status}`),element('small',op.operation_id),element('p',`Bước: ${op.phase||''}`));if(op.error)card.append(element('p',op.error,'warning'));if(op.result){const d=element('details');d.append(element('summary','Dữ liệu trả về'),element('pre',JSON.stringify(op.result,null,2)));card.append(d);}portal.operationExtras(card,op);$('#operations').append(card);}}
async function command(eid,action){const scope=eid+'/'+action;track(await post('/environments/'+eid+'/'+action,{},idempotency(scope)));commands.delete(scope);await refresh();}
async function extend(eid){const scope=eid+'/extend';const r=await post('/environments/'+eid+'/extend',{hours:1},idempotency(scope));commands.delete(scope);notice('Hạn mới: '+timestamp(r.deadline));await refresh();}
async function refresh(){
  if(!accessToken)return;
  const [envs,backups]=await Promise.all([api('/environments'),api('/recovery-points'),portal.refreshOwn()]);
  cachedEnvironments=envs.items;renderEnvironments();portal.renderHistory(cachedEnvironments);
  $('#backups').replaceChildren();
  for(const rp of backups.items){
    const card=element('article',undefined,'card');
    card.append(element('h3',rp.template_id+' · '+(stateNames[rp.status]||rp.status)),element('small',rp.recovery_point),element('p',rp.scope),element('small','Hạn lưu: '+timestamp(rp.retain_until)),element('p',rp.restore_verified?'Đã xác minh dữ liệu sau khôi phục':'Chưa xác minh qua một lần khôi phục'));
      card.append(button('Khôi phục',()=>openCreate({template_id:rp.template_id,name:rp.template_id},rp.recovery_point),rp.status!=='AVAILABLE'||!!(rp.in_use||rp.restore_lock||rp.export_lock||rp.delete_lock)||Number(rp.retain_until)*1000<=Date.now()||!catalogCache.get(rp.template_id)?.create_enabled));
    portal.backupExtras(card,rp);$('#backups').append(card);
  }
}
function renderEnvironments(){
  $('#environments').replaceChildren();
  for(const e of portal.filter(cachedEnvironments).filter(e=>e.status!=='DELETED')){
    const card=element('article',undefined,'card');
    card.append(element('small',e.environment_id),element('h3',e.template_id+' · '+(stateNames[e.status]||e.status)),element('p','Hạn phiên: '+timestamp(e.deadline)),element('small','Dự toán: '+((e.estimate?.total_milliusd||0)/1000).toFixed(3)+' USD'));
    if(e.access_address){if(/^https?:\/\//.test(e.access_address)){const a=element('a','Mở địa chỉ truy cập');a.href=e.access_address;a.target='_blank';a.rel='noopener noreferrer';card.append(a);}else card.append(element('p',e.access_address));}
    if(e.needs_admin)card.append(element('p','Cần quản trị kiểm tra; tài nguyên có thể còn phát sinh phí.','warning'));
    portal.environmentExtras(card,e);
    const actions=element('div',undefined,'actions'),unready=e.status!=='READY'||!!e.lock;
    if(['T01','T05'].includes(e.template_id))actions.append(button('Tải khóa SSH',()=>downloadKey(e.environment_id),unready));
    if(['T02','T06','T07'].includes(e.template_id))actions.append(button('Quản lý file',()=>openFiles(e),unready));
    if(e.template_id==='T09')actions.append(button('Đọc API có đăng nhập',()=>readOwnedApi(e),unready));
    if(['T03','T04','T08','T09','T10'].includes(e.template_id))actions.append(button('Dữ liệu minh họa',()=>openRecords(e),unready));
    actions.append(button('Sao lưu',()=>command(e.environment_id,'backup'),unready),button('Gia hạn 1 giờ',()=>extend(e.environment_id),unready),button('Sao lưu và thu hồi',()=>command(e.environment_id,'destroy'),!['READY','ERROR_RETAINED','CREATE_FAILED'].includes(e.status)||!!e.lock));
    card.append(actions);
    if(e.cleanup_report){const details=element('details');details.append(element('summary','Báo cáo sau thu hồi'),element('pre',JSON.stringify(e.cleanup_report,null,2)));card.append(details);}
    $('#environments').append(card);
  }
  if(!$('#environments').children.length)$('#environments').append(element('p','Không có môi trường đang sử dụng khớp bộ lọc.'));
}
async function openFiles(env){
  fileEnvironment=env;
  const data=await api('/environments/'+env.environment_id+'/files');
  $('#file-notice').textContent=env.template_id==='T07'?'File riêng tư. Tạo liên kết để người có liên kết tải tối đa 15 phút hoặc tới hạn phiên.':'Dữ liệu minh họa của website/ứng dụng. Tối đa 32 file, mỗi file 8 KiB. Dữ liệu được phục vụ qua địa chỉ truy cập.';
  $('#file-list').replaceChildren(element('p',`Đã dùng ${data.writes_used}/128 lần đăng file.`));
  for(const file of data.items){
    const row=element('p');
    row.append(element('span',file.key+' · '+file.size+' byte'));
    if(env.template_id==='T07')row.append(button('Tạo liên kết 15 phút',async()=>{
      const share=await post('/environments/'+env.environment_id+'/share',{key:file.key});
      const a=element('a','Tải file · hết hạn '+timestamp(share.expires_at));a.href=share.url;a.target='_blank';a.rel='noopener noreferrer';row.append(a);notice(share.notice);
    }));
    else {const a=element('a','Mở file');a.href=env.access_address+(env.template_id==='T06'?'/files/':'/')+file.key.split('/').map(encodeURIComponent).join('/');a.target='_blank';a.rel='noopener noreferrer';row.append(a);}
    $('#file-list').append(row);
  }
  $('#file-form').reset();$('#files-dialog').showModal();
}
async function submitFile(ev){
  ev.preventDefault();
  const file=$('#file-input').files[0];
  if(!file||!fileEnvironment)return;
  if(file.size>8192)throw Error('Mỗi file tối đa 8 KiB.');
  const extension=file.name.split('.').pop().toLowerCase();
  const types={html:'text/html',htm:'text/html',css:'text/css',js:'application/javascript',json:'application/json',txt:'text/plain',png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',svg:'image/svg+xml'};
  const type=types[extension];
  if(!type)throw Error('Chọn HTML, CSS, JavaScript, JSON, TXT, PNG, JPEG hoặc SVG.');
  const bytes=new Uint8Array(await file.arrayBuffer());
  const body={key:$('#file-key').value,content_base64:btoa(String.fromCharCode(...bytes)),content_type:type};
  const scope=fileEnvironment.environment_id+'/files/'+JSON.stringify(body);
  $('#file-submit').disabled=true;
  try{
    const op=await post('/environments/'+fileEnvironment.environment_id+'/files',body,idempotency(scope));
    commands.delete(scope);track(op);$('#files-dialog').close();await refresh();
  }finally{$('#file-submit').disabled=false;}
}
async function openRecords(env){recordEnvironment=env;$('#record-form').reset();$('#record-notice').textContent=env.template_id==='T10'?'Nạp dữ liệu từ S3 sẽ thay bảng minh họa bằng một dòng CSV. Bản sao bảo vệ cả bảng và file S3.':'Tối đa 32 bản ghi, mỗi nội dung 256 byte. Đăng lại cùng mã để cập nhật.';$('#record-submit').textContent=env.template_id==='T10'?'Nạp S3 vào kho phân tích':'Lưu bản ghi';$('#records-dialog').showModal();}
async function recordCommand(action,body={}){const eid=recordEnvironment.environment_id,scope=eid+'/'+action+'/'+JSON.stringify(body);track(await post('/environments/'+eid+'/'+action,body,idempotency(scope)));commands.delete(scope);$('#records-dialog').close();await refresh();}
$('#record-form').onsubmit=ev=>{ev.preventDefault();const body={record_id:$('#record-id').value,text:$('#record-text').value};if(new TextEncoder().encode(body.text).length>256)return notice('Nội dung tối đa 256 byte UTF-8.');recordCommand(recordEnvironment.template_id==='T10'?'load_dataset':'put_record',body).catch(showError);};
$('#record-read').onclick=()=>recordCommand(recordEnvironment.template_id==='T10'?'query_demo':'list_records').catch(showError);
$('#record-close').onclick=()=>$('#records-dialog').close();
$('#file-form').onsubmit=ev=>submitFile(ev).catch(showError);
$('#file-close').onclick=()=>$('#files-dialog').close();
$('#file-input').onchange=()=>{const file=$('#file-input').files[0];if(file)$('#file-key').value=file.name;};
const authentication=createCognitoAuth({cfg,onAuthenticated:async tokens=>{accessToken=tokens.access_token;refreshToken=tokens.refresh_token;sessionStorage.setItem('cloudhub_access_token',accessToken);sessionStorage.setItem('cloudhub_auth_method','api');if(refreshToken)sessionStorage.setItem('cloudhub_refresh_token',refreshToken);await start();}});
$('#login').onclick=()=>login().catch(showError);$('#logout').onclick=()=>logout().catch(showError);$('#refresh').onclick=()=>refresh().catch(showError);$('#cancel').onclick=()=>$('#create-dialog').close();$('#hours').onchange=()=>updateQuote().catch(showError);$('#create-form').onsubmit=submitCreate;
async function start(){await callback();$('#login').hidden=!!accessToken;$('#logout').hidden=!accessToken;if(!cfg.configured){notice('Chưa triển khai AWS. Cấu hình và minh chứng thật đang chờ xác nhận.');return;}if(!accessToken){notice('Đăng nhập để xem danh mục và quản lý môi trường.');return;}notice('Đã đăng nhập. Chỉ mẫu đã kiểm chứng mới cho phép tạo.');await Promise.all([loadCatalog(),refresh()]);if(portal.isAdmin())await portal.loadAdmin();}
setInterval(async()=>{if(!accessToken||document.hidden)return;try{for(const [id,op]of operations){if(!['SUCCEEDED','FAILED','CANCELLED'].includes(op.status))operations.set(id,await api(op.admin_actor&&portal.isAdmin()?'/admin/operations/'+id:'/operations/'+id));}renderOperations();await refresh();}catch(e){showError(e);}},10000);
async function mutate(path,body={}){const scope=path+'/'+JSON.stringify(body),result=await post(path,body,idempotency(scope));commands.delete(scope);return result;}
const portal=createPortal({api,post,notice,element,button,timestamp,track,refresh,operations,renderOperations,cfg,getToken:()=>accessToken,ensureToken,mutate});
for(const id of ['environment-search','environment-status','environment-template'])$('#'+id).addEventListener(id==='environment-search'?'input':'change',renderEnvironments);
start().catch(showError);

async function readOwnedApi(env){await ensureToken();const response=await fetch(env.access_address,{headers:{Authorization:'Bearer '+accessToken}});const value=await response.json();if(!response.ok)throw Error('Không đọc được API: '+response.status);notice(JSON.stringify(value,null,2));}

function tokenClaims(token){const payload=token.split('.')[1].replace(/-/g,'+').replace(/_/g,'/');return JSON.parse(atob(payload));}
async function ensureToken(){
  if(!accessToken)throw Error('Hãy đăng nhập để tiếp tục.');
  if(Number(tokenClaims(accessToken).exp)*1000>Date.now()+60000)return;
  if(!refreshToken)throw Error('Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.');
  if(!refreshPromise)refreshPromise=(async()=>{
    let value;
    if(sessionStorage.getItem('cloudhub_auth_method')==='api')value=await refreshCognito(cfg,refreshToken,accessToken);
    else{const response=await fetch(cfg.cognitoDomain+'/oauth2/token',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({grant_type:'refresh_token',client_id:cfg.clientId,refresh_token:refreshToken})});if(!response.ok)throw Error('Không làm mới được phiên. Hãy đăng nhập lại.');value=await response.json();}
    const before=tokenClaims(accessToken),after=tokenClaims(value.access_token);
    if(before.iss!==after.iss||before.sub!==after.sub)throw Error('Danh tính đăng nhập thay đổi. Hãy đăng nhập lại.');
    accessToken=value.access_token;refreshToken=value.refresh_token||refreshToken;
    sessionStorage.setItem('cloudhub_access_token',accessToken);sessionStorage.setItem('cloudhub_refresh_token',refreshToken);
  })().finally(()=>{refreshPromise=null;});
  await refreshPromise;
}
