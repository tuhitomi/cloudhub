"""Keep long evidence runs authenticated; tokens stay in memory/environment, never logs."""
import base64,json,os,re,time,urllib.parse,urllib.request
def claims(token):
    payload=token.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(payload+'='*((4-len(payload)%4)%4)))
class TokenProvider:
    def __init__(self,prefix='CLOUDHUB_'):
        self.token=os.environ[prefix+'ACCESS_TOKEN'];self.refresh=os.getenv(prefix+'REFRESH_TOKEN')
        self.domain=os.getenv('CLOUDHUB_COGNITO_DOMAIN','');self.client=os.getenv('CLOUDHUB_CLIENT_ID','')
        self.method=os.getenv(prefix+'AUTH_METHOD','oauth');self.region=os.getenv('CLOUDHUB_REGION','')
    def get(self):
        if float(claims(self.token)['exp'])>time.time()+60:return self.token
        if not self.refresh:raise RuntimeError('Access token expiring; use an approved Cognito refresh token for long runs')
        if not re.fullmatch(r'https://[a-z0-9-]+\.auth\.[a-z0-9-]+\.amazoncognito\.com',self.domain) or not self.client:raise ValueError('Need the approved Cognito domain/client outputs')
        if self.method=='api':
            if not re.fullmatch(r'[a-z]{2}-[a-z]+-\d',self.region):raise ValueError('Need the approved AWS region')
            before=claims(self.token)
            if not before['iss'].startswith('https://cognito-idp.'+self.region+'.amazonaws.com/') or before.get('client_id')!=self.client or before.get('token_use')!='access':raise ValueError('Token does not belong to the configured regional Cognito client')
            request=urllib.request.Request('https://cognito-idp.'+self.region+'.amazonaws.com/',data=json.dumps({'AuthFlow':'REFRESH_TOKEN_AUTH','ClientId':self.client,'AuthParameters':{'REFRESH_TOKEN':self.refresh}}).encode(),
                headers={'Content-Type':'application/x-amz-json-1.1','X-Amz-Target':'AWSCognitoIdentityProviderService.InitiateAuth'})
        else:
            request=urllib.request.Request(self.domain+'/oauth2/token',data=urllib.parse.urlencode({'grant_type':'refresh_token','client_id':self.client,'refresh_token':self.refresh}).encode(),
                headers={'Content-Type':'application/x-www-form-urlencoded'})
        try:
            with urllib.request.urlopen(request,timeout=10) as response:value=json.loads(response.read())
        except Exception:raise RuntimeError('Cognito token refresh failed; sign in again') from None
        if self.method=='api':value={'access_token':value['AuthenticationResult']['AccessToken'],'refresh_token':value['AuthenticationResult'].get('RefreshToken',self.refresh)}
        if self.method=='api' and (claims(value['access_token']).get('client_id')!=self.client or claims(value['access_token']).get('token_use')!='access'):raise RuntimeError('Invalid Cognito access token; stop')
        if (claims(value['access_token'])['iss'],claims(value['access_token'])['sub'])!=(claims(self.token)['iss'],claims(self.token)['sub']):raise RuntimeError('Refreshed identity changed; stop')
        self.token=value['access_token'];self.refresh=value.get('refresh_token',self.refresh);return self.token
