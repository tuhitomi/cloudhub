"""Trusted SSM helper on both T08 nodes. Only registered /shared/records data is editable."""
import base64,hashlib,json,os,pathlib,re,sys
root=pathlib.Path('/shared/records')
def records():
    result=[]
    for path in sorted(root.iterdir()):
        if re.fullmatch(r'\.[A-Za-z0-9_-]{1,64}\.tmp',path.name) and not path.is_symlink():continue
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',path.name):raise ValueError('unexpected data')
        data=path.read_bytes()
        if len(data)>256:raise ValueError('record size')
        result.append({'record_id':path.name,'text':data.decode()})
        if len(result)>32:raise ValueError('record count')
    return result
if __name__=='__main__':
    if len(sys.argv)>1:
        item=json.loads(base64.b64decode(sys.argv[1]))
        key=item['record_id'];data=item['text'].encode()
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',key) or len(data)>256:raise ValueError('invalid input')
        if len(records())>=32 and not (root/key).exists():raise ValueError('record count')
        # Atomic replacement; repeated op with the same bytes is harmless.
        temporary=root/('.'+key+'.tmp');temporary.write_bytes(data);os.replace(temporary,root/key)
    items=records();serialized=json.dumps(items,sort_keys=True,separators=(',',':')).encode()
    print(json.dumps({'items':items,'sha256':hashlib.sha256(serialized).hexdigest()},ensure_ascii=False))
