import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {parseHTML} from 'linkedom';
import {createCognitoAuth,normalizedTokens,refreshCognito} from '../frontend/auth.js';
const html=await readFile(new URL('../frontend/index.html',import.meta.url),'utf8');
const cfg={configured:true,region:'ap-southeast-1',clientId:'client123'};
const token=(sub='alice',use='access')=>'head.'+Buffer.from(JSON.stringify({sub,token_use:use,client_id:cfg.clientId,iss:'https://cognito-idp.ap-southeast-1.amazonaws.com/pool',exp:Date.now()/1000+3600})).toString('base64url')+'.signature';
function setup(responses){
  const {window}=parseHTML(html);globalThis.document=window.document;
  const $=s=>document.querySelector(s),calls=[],signed=[];
  for(const dialog of document.querySelectorAll('dialog')){dialog.showModal=()=>dialog.setAttribute('open','');dialog.close=()=>dialog.removeAttribute('open');}
  $('#login-form').reset=()=>{for(const input of $('#login-form').querySelectorAll('input'))input.value='';};
  globalThis.fetch=async(url,options)=>{calls.push({url,options,body:JSON.parse(options.body)});return {ok:responses[0].ok??true,json:async()=>responses.shift().body};};
  const auth=createCognitoAuth({cfg,onAuthenticated:async value=>signed.push(value)});
  const submit=()=>$('#login-form').onsubmit({preventDefault(){}});
  return {$,auth,calls,signed,submit};
}
test('sign-in posts only to regional Cognito, clears password and returns real token fields',async()=>{
  const {$,auth,calls,signed,submit}=setup([{body:{AuthenticationResult:{AccessToken:token(),RefreshToken:'refresh'}}}]);auth.open();$('#login-email').value='alice@example.com';$('#login-password').value='own-test-password';await submit();
  assert.equal(calls[0].url,'https://cognito-idp.ap-southeast-1.amazonaws.com/');assert.equal(calls[0].body.AuthFlow,'USER_PASSWORD_AUTH');assert.equal(calls[0].body.AuthParameters.USERNAME,'alice@example.com');assert.equal($('#login-password').value,'');assert.equal(signed.length,1);assert.equal($('#login-dialog').hasAttribute('open'),false);
});
test('first login waits for a user-chosen new password and never stores the temporary password',async()=>{
  const {$,auth,calls,signed,submit}=setup([{body:{ChallengeName:'NEW_PASSWORD_REQUIRED',Session:'challenge',ChallengeParameters:{requiredAttributes:'[]'}}},{body:{AuthenticationResult:{AccessToken:token(),RefreshToken:'refresh'}}}]);auth.open();$('#login-email').value='alice@example.com';$('#login-password').value='temporary';await submit();assert.equal(signed.length,0);assert.equal($('#login-new-fields').hidden,false);assert.equal($('#login-password').value,'');$('#login-new-password').value='chosen-by-user';await submit();assert.equal(calls[1].body.ChallengeName,'NEW_PASSWORD_REQUIRED');assert.equal(calls[1].body.ChallengeResponses.NEW_PASSWORD,'chosen-by-user');assert.equal($('#login-new-password').value,'');assert.equal(signed.length,1);
});
test('provider errors clear password and never authenticate',async()=>{
  const {$,auth,signed,submit}=setup([{ok:false,body:{__type:'NotAuthorizedException',message:'do not display raw data'}}]);auth.open();$('#login-email').value='alice@example.com';$('#login-password').value='secret';await submit();assert.equal($('#login-password').value,'');assert.equal(signed.length,0);assert.match($('#login-notice').textContent,/không hợp lệ/);assert.doesNotMatch($('#login-notice').textContent,/raw data/);
});
test('refresh rejects identity changes and ID tokens',async()=>{
  assert.throws(()=>normalizedTokens(cfg,{AuthenticationResult:{AccessToken:token('alice','id')}}),/không hợp lệ/);
  globalThis.fetch=async()=>({ok:true,json:async()=>({AuthenticationResult:{AccessToken:token('bob')}})});
  await assert.rejects(refreshCognito(cfg,'refresh',token('alice')),/Danh tính/);
});
test('unsupported challenges fail closed and can be cancelled without authenticating',async()=>{
  const {$,auth,signed,submit}=setup([{body:{ChallengeName:'CUSTOM_CHALLENGE',Session:'challenge'}}]);auth.open();$('#login-email').value='alice@example.com';$('#login-password').value='secret';await submit();assert.equal(signed.length,0);assert.match($('#login-notice').textContent,/chưa được hỗ trợ/);$('#login-close').onclick();assert.equal($('#login-dialog').hasAttribute('open'),false);assert.equal($('#login-password').value,'');
});
