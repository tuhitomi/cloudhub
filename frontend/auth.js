// Passwords go directly over HTTPS to Cognito. Only tokens enter sessionStorage.
export async function cognitoRequest(cfg, action, body){
  if(!cfg.configured || !/^[a-z]{2}-[a-z]+-\d$/.test(cfg.region) || !/^[a-z0-9]{1,128}$/.test(cfg.clientId))throw Error('Chưa cấu hình đăng nhập.');
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{
    const response=await fetch('https://cognito-idp.'+cfg.region+'.amazonaws.com/',{method:'POST',headers:{'Content-Type':'application/x-amz-json-1.1','X-Amz-Target':'AWSCognitoIdentityProviderService.'+action},body:JSON.stringify(body),signal:controller.signal});
    const data=await response.json();
    if(!response.ok){
      const code=(data.__type||'').split('#').pop();
      const messages={NotAuthorizedException:'Email hoặc mật khẩu không hợp lệ.',UserNotFoundException:'Email hoặc mật khẩu không hợp lệ.',InvalidPasswordException:'Mật khẩu cần ít nhất 12 ký tự, có chữ hoa, chữ thường, số và ký hiệu.',PasswordResetRequiredException:'Hãy dùng Quên mật khẩu để đặt lại mật khẩu.',UserNotConfirmedException:'Tài khoản cần được xác nhận trước khi đăng nhập.',CodeMismatchException:'Mã xác thực không đúng.',ExpiredCodeException:'Mã xác thực đã hết hạn.',TooManyRequestsException:'Có quá nhiều yêu cầu. Hãy thử lại sau.',InvalidParameterException:'Kiểm tra thông tin đăng nhập hoặc mã xác thực.'};
      throw Error(messages[code]||'Không thực hiện được yêu cầu đăng nhập.');
    }
    return data;
  }catch(error){if(error.name==='AbortError'||error instanceof TypeError)throw Error('Không kết nối được hệ thống đăng nhập. Hãy thử lại.');throw error;}
  finally{clearTimeout(timeout);}
}

export function claims(token){
  const payload=token.split('.')[1]?.replace(/-/g,'+').replace(/_/g,'/');
  if(!payload)throw Error('Phiên đăng nhập không hợp lệ.');
  return JSON.parse(atob(payload));
}

export function normalizedTokens(cfg, result, previous){
  const value=result.AuthenticationResult;
  if(!value?.AccessToken)throw Error('Chưa hoàn tất xác thực.');
  const after=claims(value.AccessToken);
  if(after.token_use!=='access'||after.client_id!==cfg.clientId||!after.sub||!Number.isFinite(Number(after.exp))||Number(after.exp)*1000<=Date.now())throw Error('Phiên đăng nhập không hợp lệ.');
  if(previous){const before=claims(previous);if(before.iss!==after.iss||before.sub!==after.sub)throw Error('Danh tính đăng nhập thay đổi. Hãy đăng nhập lại.');}
  return {access_token:value.AccessToken,refresh_token:value.RefreshToken||null};
}

export async function refreshCognito(cfg, token, previous){
  return normalizedTokens(cfg,await cognitoRequest(cfg,'InitiateAuth',{AuthFlow:'REFRESH_TOKEN_AUTH',ClientId:cfg.clientId,AuthParameters:{REFRESH_TOKEN:token}}),previous);
}

export function createCognitoAuth({cfg,onAuthenticated}){
  const $=selector=>document.querySelector(selector);
  let challenge=null,username='',requesting=false;
  function clear(){challenge=null;username='';$('#login-form').reset();$('#login-new-fields').hidden=true;$('#login-code-fields').hidden=true;$('#login-password-fields').hidden=false;$('#login-password').required=true;$('#login-new-password').required=false;$('#login-code').required=false;$('#login-email').readOnly=false;$('#login-submit').textContent='Đăng nhập';}
  function cancel(){if(requesting)return;clear();$('#login-dialog').close();}
  function open(){if(!cfg.configured)throw Error('Chưa cấu hình đăng nhập.');clear();$('#login-notice').textContent='';$('#login-dialog').showModal();$('#login-email').focus();}
  $('#login-close').onclick=cancel;
  $('#login-dialog').addEventListener('cancel',event=>{event.preventDefault();cancel();});
  $('#login-form').onsubmit=async event=>{
    event.preventDefault();if(requesting)return;requesting=true;$('#login-submit').disabled=true;$('#login-close').disabled=true;$('#login-notice').textContent='Đang xác thực…';
    try{
      let response;
      if(!challenge){username=$('#login-email').value.trim();response=await cognitoRequest(cfg,'InitiateAuth',{ClientId:cfg.clientId,AuthFlow:'USER_PASSWORD_AUTH',AuthParameters:{USERNAME:username,PASSWORD:$('#login-password').value}});}
      else{
        const values={USERNAME:username};
        if(challenge.ChallengeName==='NEW_PASSWORD_REQUIRED')values.NEW_PASSWORD=$('#login-new-password').value;
        else{const keys={SMS_MFA:'SMS_MFA_CODE',SOFTWARE_TOKEN_MFA:'SOFTWARE_TOKEN_MFA_CODE',EMAIL_OTP:'EMAIL_OTP_CODE',SMS_OTP:'SMS_OTP_CODE'};values[keys[challenge.ChallengeName]]=$('#login-code').value.trim();}
        response=await cognitoRequest(cfg,'RespondToAuthChallenge',{ClientId:cfg.clientId,ChallengeName:challenge.ChallengeName,Session:challenge.Session,ChallengeResponses:values});
      }
      if(response.AuthenticationResult){const tokens=normalizedTokens(cfg,response);clear();$('#login-dialog').close();await onAuthenticated(tokens);return;}
      if(!response.Session||!['NEW_PASSWORD_REQUIRED','SMS_MFA','SOFTWARE_TOKEN_MFA','EMAIL_OTP','SMS_OTP'].includes(response.ChallengeName))throw Error('Tài khoản yêu cầu phương thức xác thực chưa được hỗ trợ. Liên hệ quản trị.');
      if(response.ChallengeName==='NEW_PASSWORD_REQUIRED'&&JSON.parse(response.ChallengeParameters?.requiredAttributes||'[]').length)throw Error('Tài khoản thiếu thông tin bắt buộc. Liên hệ quản trị.');
      challenge=response;username=response.ChallengeParameters?.USER_ID_FOR_SRP||username;
      const password=challenge.ChallengeName==='NEW_PASSWORD_REQUIRED';$('#login-password-fields').hidden=true;$('#login-password').required=false;$('#login-new-fields').hidden=!password;$('#login-new-password').required=password;$('#login-code-fields').hidden=password;$('#login-code').required=!password;$('#login-email').readOnly=true;
      $('#login-submit').textContent=password?'Đặt mật khẩu và đăng nhập':'Xác thực';$('#login-notice').textContent=password?'Đây là lần đăng nhập đầu. Hãy tự đặt mật khẩu mới.':'Nhập mã xác thực của tài khoản.';
    }catch(error){$('#login-notice').textContent=error.message;}
    finally{$('#login-password').value='';$('#login-new-password').value='';$('#login-code').value='';requesting=false;$('#login-submit').disabled=false;$('#login-close').disabled=false;}
  };
  return {open};
}
