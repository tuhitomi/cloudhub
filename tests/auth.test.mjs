import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {parseHTML} from 'linkedom';
import {createCognitoAuth,createRegistration,normalizedTokens,refreshCognito} from '../frontend/auth.js';
const html=await readFile(new URL('../frontend/index.html',import.meta.url),'utf8');
const cfg={configured:true,region:'ap-southeast-1',clientId:'client123'};
const token=(sub='alice',use='access')=>'head.'+Buffer.from(JSON.stringify({sub,token_use:use,client_id:cfg.clientId,iss:'https://cognito-idp.ap-southeast-1.amazonaws.com/pool',exp:Date.now()/1000+3600})).toString('base64url')+'.signature';
function setup(responses){
  const {window}=parseHTML(html);globalThis.document=window.document;
  const $=s=>document.querySelector(s),calls=[],signed=[];
  for(const dialog of document.querySelectorAll('dialog')){dialog.showModal=()=>dialog.setAttribute('open','');dialog.close=()=>dialog.removeAttribute('open');}
  for(const form of document.querySelectorAll('form'))form.reset=()=>{for(const input of form.querySelectorAll('input'))input.value='';};
  globalThis.fetch=async(url,options)=>{calls.push({url,options,body:JSON.parse(options.body)});return {ok:responses[0].ok??true,json:async()=>responses.shift().body};};
  const auth=createCognitoAuth({cfg,onAuthenticated:async value=>signed.push(value)});
  const submit=()=>$('#login-form').onsubmit({preventDefault(){}});
  return {$,auth,calls,signed,submit,cfg};
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

function registrationSetup(responses){
  const state=setup(responses),confirmed=[];
  const registration=createRegistration({cfg,onConfirmed:email=>confirmed.push(email)});
  const signup=async()=>{registration.open();state.$('#signup-email').value='New@Example.com';state.$('#signup-password').value='TestPassword123!';state.$('#signup-password-confirm').value='TestPassword123!';await state.$('#signup-form').onsubmit({preventDefault(){}});};
  return {...state,registration,confirmed,signup,verify:()=>state.$('#verification-form').onsubmit({preventDefault(){}})};
}
test('signup sends only email/password to Cognito and waits for verification, with no role or quota fields',async()=>{
  const {$,calls,confirmed,signup}=registrationSetup([{body:{UserConfirmed:false,UserSub:'trusted-sub'}}]);await signup();
  assert.deepEqual(Object.keys(calls[0].body).sort(),['ClientId','Password','UserAttributes','Username']);assert.equal(calls[0].body.Username,'new@example.com');
  assert.equal($('#signup-password').value,'');assert.equal($('#signup-password-confirm').value,'');assert.equal($('#verification-dialog').hasAttribute('open'),true);assert.equal(confirmed.length,0);
});
test('duplicate email does not authenticate or open a false confirmation success',async()=>{
  const {$,signup,confirmed}=registrationSetup([{ok:false,body:{__type:'UsernameExistsException'}}]);await signup();
  assert.match($('#signup-notice').textContent,/đã có tài khoản/);assert.equal($('#verification-dialog').hasAttribute('open'),false);assert.equal(confirmed.length,0);assert.equal($('#signup-password').value,'');
});
for(const [code,message] of [['CodeMismatchException',/không đúng/],['ExpiredCodeException',/hết hạn/]]){
  test('confirmation rejects '+code+' and retains the verification screen',async()=>{
    const {$,registration,verify,confirmed}=registrationSetup([{ok:false,body:{__type:code}}]);registration.openVerification('new@example.com');$('#verification-code').value='000000';await verify();
    assert.match($('#verification-notice').textContent,message);assert.equal(confirmed.length,0);assert.equal($('#verification-dialog').hasAttribute('open'),true);assert.equal($('#verification-code').value,'');
  });
}
test('resend requests a fresh Cognito code and throttles repeated clicks locally',async()=>{
  const {$,registration,calls}=registrationSetup([{body:{CodeDeliveryDetails:{DeliveryMedium:'EMAIL'}}}]);registration.openVerification('new@example.com');
  await $('#verification-resend').onclick();await $('#verification-resend').onclick();assert.equal(calls.length,1);assert.equal(calls[0].options.headers['X-Amz-Target'],'AWSCognitoIdentityProviderService.ResendConfirmationCode');assert.match($('#verification-notice').textContent,/Chờ/);
});
test('confirmation success opens login only after Cognito succeeds and passes only email',async()=>{
  const {$,registration,calls,verify,confirmed}=registrationSetup([{body:{}}]);registration.openVerification('new@example.com');$('#verification-code').value='123456';await verify();
  assert.equal(calls[0].body.ConfirmationCode,'123456');assert.deepEqual(confirmed,['new@example.com']);assert.equal($('#verification-dialog').hasAttribute('open'),false);
});
test('unconfirmed login leads to the email verification flow without issuing a session',async()=>{
  const {$,signed}=setup([{ok:false,body:{__type:'UserNotConfirmedException'}}]);let pending;
  const auth=createCognitoAuth({cfg,onAuthenticated:value=>signed.push(value),onUnconfirmed:email=>pending=email});auth.open();$('#login-email').value='pending@example.com';$('#login-password').value='wrong';await $('#login-form').onsubmit({preventDefault(){}});
  assert.equal(pending,'pending@example.com');assert.equal(signed.length,0);assert.equal($('#login-password').value,'');
});
