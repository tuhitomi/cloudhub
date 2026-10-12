"""Bounded transport retries; POST keeps one idempotency key and exact body."""
import json,time,uuid,urllib.request,urllib.error

def api_call(base,path,body,token,events=None,timeout=10):
    key=uuid.uuid4().hex if body is not None else None
    payload=json.dumps(body).encode() if body is not None else None
    auth=token();start=time.perf_counter()
    for attempt in range(1,4):
        headers={'Authorization':'Bearer '+auth}
        if key:headers.update({'Content-Type':'application/json','Idempotency-Key':key})
        request=urllib.request.Request(base.rstrip('/')+path,headers=headers,data=payload)
        try:
            with urllib.request.urlopen(request,timeout=timeout) as response:
                value=json.loads(response.read());status=response.status
            return value,{'path':path,'http_status':status,'latency_ms':(time.perf_counter()-start)*1000,'transport_retries':attempt-1}
        except (urllib.error.URLError,TimeoutError,ConnectionError) as exc:
            if isinstance(exc,urllib.error.HTTPError):raise
            reason=getattr(exc,'reason',exc)
            if not isinstance(reason,(TimeoutError,ConnectionError)):raise
            if events is not None:events.append({'path':path,'attempt':attempt,'error_type':type(reason).__name__,'elapsed_ms':(time.perf_counter()-start)*1000})
            if attempt==3:raise
            auth=token()
