"""Deterministic gateway-only free-model router. SPDX-License-Identifier: MIT"""
import base64
import hashlib
import hmac
import json
import math
import os
import re
import socket
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def dumps(x):
    return json.dumps(x, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def loads(x):
    def pairs(rows):
        result={}
        for key,value in rows:
            if key in result: raise Invalid('duplicate JSON field')
            result[key]=value
        return result
    return json.loads(x, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


ROWS = [
 ('thinkingmachines/inkling-2-small',1475,False,True,1048576),
 ('nvidia/nemotron-3.5-super-vl',1303,True,True,1000000),
 ('nvidia/nemotron-3-ultra-550b-a55b',1285,False,False,1000000),
 ('nvidia/nemotron-3.5-lightning',1221,False,False,1000000),
 ('google/gemma-4-31b-it',1210,True,True,262144),
 ('cohere/north-mini-code',1200,False,False,256000),
 ('baidu/ernie-5.5-flash-preview',1150,True,False,1024000),
 ('dots-studio/dots-3-note-preview',1150,True,True,512000),
 ('google/gemma-4-26b-a4b-it',1150,True,True,262144),
 ('inclusionai/ling-3.2-flash',1150,True,True,1048576),
 ('liquid/lfm-3-1.7b',1150,True,False,262144),
 ('nvidia/nemotron-3-nano-omni-30b-a3b-reasoning',1150,False,True,256000),
 ('particle-ai/particle-1',1150,True,True,1048576),
 ('recursive/flint-v1.0',1150,True,False,262144)]
MODELS = [dict(id='openrouter/'+n+':free',rating=r,json=j,vision=v,context=c) for n,r,j,v,c in ROWS]
CATALOG = {'providers':[{'id':'openrouter','base_url':'https://openrouter.ai/api/v1','key_env':'OPENROUTER_API_KEY','wire':'openai','models':[{'id':m['id'],'free':True,'context_length':m['context']} for m in MODELS]}]}
CATALOG_TEXT = dumps(CATALOG)
CAP = 8192
LIMIT = 2 * 1024 * 1024
CLIENT_KEY = 'makefun-client' # Public credential used by the kit's environment driver.
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


class Invalid(ValueError):
    pass


def need(ok, msg):
    if not ok:
        raise Invalid(msg)


def text_blocks(value):
    if isinstance(value,str):
        return value
    need(isinstance(value,list), 'expected text or text blocks')
    parts=[]
    for b in value:
        need(isinstance(b,dict) and b.get('type')=='text' and isinstance(b.get('text'),str), 'unsupported text block')
        need(set(b)<= {'type','text'}, 'unsupported text block fields')
        parts.append(b['text'])
    return '\n'.join(parts)


def content(value):
    if isinstance(value,str) or value is None:
        return value
    need(isinstance(value,list), 'invalid content')
    out=[]
    for b in value:
        need(isinstance(b,dict), 'invalid content block')
        if b.get('type')=='text':
            need(set(b)<= {'type','text'}, 'unsupported text block fields')
            need(isinstance(b.get('text'),str), 'invalid text')
            out.append({'type':'text','text':b['text']})
        elif b.get('type')=='image_url':
            need(set(b)<= {'type','image_url'}, 'unsupported image block fields')
            im=b.get('image_url')
            need(isinstance(im,dict) and isinstance(im.get('url'),str), 'invalid image_url')
            need(set(im)<= {'url','detail'} and im.get('detail','auto') in ('auto','low','high'), 'invalid image options')
            need(im['url'].startswith(('https://','http://','data:image/')), 'unsupported image URL')
            out.append(b)
        else:
            raise Invalid('unsupported content block')
    return out


def translate(b, anth):
    need(isinstance(b,dict), 'body must be object')
    common={'model','messages','stream','max_tokens','temperature','top_p','tools','tool_choice'}
    allowed=common | ({'system','stop_sequences','top_k'} if anth else {'max_completion_tokens','response_format','stop','stream_options','frequency_penalty','presence_penalty','seed','n','parallel_tool_calls','user','logit_bias'})
    need(not (set(b)-allowed), 'unsupported request fields: '+','.join(sorted(set(b)-allowed)))
    need(b.get('model')=='auto', 'model must be auto')
    need(type(b.get('stream',False)) is bool, 'stream must be boolean')
    need(isinstance(b.get('messages'),list) and b['messages'], 'messages must be nonempty array')
    need(not ('max_tokens' in b and 'max_completion_tokens' in b), 'conflicting output limits')
    cap=b.get('max_completion_tokens',b.get('max_tokens',1024))
    need(type(cap) is int and cap>0, 'output limit must be positive integer')
    out={'messages':[],'stream':False,'max_tokens':min(cap,CAP)}
    for k in ('temperature','top_p','frequency_penalty','presence_penalty'):
        if k in b:
            need(type(b[k]) in (int,float) and math.isfinite(b[k]), 'invalid '+k)
            lo,hi={'temperature':(0,1 if anth else 2),'top_p':(0,1),'frequency_penalty':(-2,2),'presence_penalty':(-2,2)}[k]
            need(lo<=b[k]<=hi, 'out of range '+k)
            out[k]=b[k]
    need('top_k' not in b, 'top_k is unsupported by Chat Completions')
    if 'n' in b:
        need(type(b['n']) is int and b['n']==1, 'only n=1 supported')
    for k in ('seed','parallel_tool_calls','user','logit_bias'):
        if k in b:
            need((k=='seed' and type(b[k]) is int) or (k=='parallel_tool_calls' and type(b[k]) is bool) or (k=='user' and isinstance(b[k],str)) or (k=='logit_bias' and isinstance(b[k],dict) and all(isinstance(v,(int,float)) and not isinstance(v,bool) and -100<=v<=100 for v in b[k].values())), 'invalid '+k)
            out[k]=b[k]
    if 'stream_options' in b:
        need(isinstance(b['stream_options'],dict) and set(b['stream_options'])<= {'include_usage'} and type(b['stream_options'].get('include_usage',False)) is bool, 'invalid stream_options')
    if anth and 'system' in b:
        out['messages'].append({'role':'system','content':text_blocks(b['system'])})
    for m in b['messages']:
        need(isinstance(m,dict), 'invalid message')
        role=m.get('role')
        need(role in (('user','assistant') if anth else ('system','developer','user','assistant','tool')), 'invalid role')
        need(not(set(m)-({'role','content'} if anth else {'role','content','name','tool_calls','tool_call_id'})), 'unsupported message fields')
        if not anth:
            need('content' in m or role=='assistant' and bool(m.get('tool_calls')), 'message lacks content')
            nm=dict(m); nm['content']=content(m.get('content'))
            need(role=='assistant' or nm['content'] is not None, 'message content cannot be null')
            if 'name' in m: need(isinstance(m['name'],str), 'invalid message name')
            if role=='tool':
                need(isinstance(m.get('tool_call_id'),str), 'tool result lacks id')
            if 'tool_calls' in m:
                need(role=='assistant' and bool(m['tool_calls']), 'tool calls require assistant role')
                validate_calls(m['tool_calls'],None)
            need(role=='tool' or 'tool_call_id' not in m, 'tool_call_id requires tool role')
            out['messages'].append(nm)
            continue
        val=m.get('content')
        if isinstance(val,str):
            out['messages'].append({'role':role,'content':val}); continue
        need(isinstance(val,list), 'invalid Messages content')
        pending=[]; calls=[]
        def flush():
            if pending or calls:
                nm={'role':role,'content':list(pending) if pending else None}
                if calls: nm['tool_calls']=list(calls)
                out['messages'].append(nm)
                pending.clear(); calls.clear()
        for block in val:
            need(isinstance(block,dict), 'invalid block')
            typ=block.get('type')
            if typ=='text':
                need(set(block)<= {'type','text'}, 'unsupported text block fields')
                need(isinstance(block.get('text'),str), 'invalid text')
                pending.append({'type':'text','text':block['text']})
            elif typ=='image':
                need(set(block)<= {'type','source'}, 'unsupported image fields')
                s=block.get('source',{})
                need(role=='user' and isinstance(s,dict), 'invalid image')
                if s.get('type')=='base64':
                    need(set(s)<= {'type','media_type','data'}, 'unsupported image source fields')
                    need(s.get('media_type') in ('image/jpeg','image/png','image/gif','image/webp') and isinstance(s.get('data'),str), 'invalid image source')
                    base64.b64decode(s['data'],validate=True)
                    url='data:'+s['media_type']+';base64,'+s['data']
                else:
                    need(set(s)<= {'type','url'}, 'unsupported image source fields')
                    need(s.get('type')=='url' and isinstance(s.get('url'),str), 'unsupported image source')
                    need(s['url'].startswith(('https://','http://')), 'unsupported image URL')
                    url=s['url']
                pending.append({'type':'image_url','image_url':{'url':url}})
            elif typ=='tool_use':
                need(set(block)<= {'type','id','name','input'}, 'unsupported tool use fields')
                need(role=='assistant' and all(isinstance(block.get(k),str) for k in ('id','name')) and isinstance(block.get('input'),dict), 'invalid tool_use')
                calls.append({'id':block['id'],'type':'function','function':{'name':block['name'],'arguments':dumps(block['input'])}})
            elif typ=='tool_result':
                need(set(block)<= {'type','tool_use_id','content','is_error'}, 'unsupported tool result fields')
                need('is_error' not in block or type(block['is_error']) is bool, 'invalid tool error flag')
                need(role=='user' and isinstance(block.get('tool_use_id'),str) and not block.get('is_error',False), 'unsupported tool result')
                flush()
                out['messages'].append({'role':'tool','tool_call_id':block['tool_use_id'],'content':text_blocks(block.get('content',''))})
            else: raise Invalid('unsupported Messages block')
        flush()
    tools=b.get('tools',[])
    need(isinstance(tools,list), 'tools must be array')
    converted=[]
    for t in tools:
        need(isinstance(t,dict), 'invalid tool')
        need(set(t)<= ({'name','description','input_schema'} if anth else {'type','function'}), 'unsupported tool fields')
        f={'name':t.get('name'),'parameters':t.get('input_schema')} if anth else t.get('function')
        need(anth or t.get('type')=='function', 'unsupported tool type')
        need(isinstance(f,dict) and isinstance(f.get('name'),str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',f['name']) and isinstance(f.get('parameters'),dict), 'invalid function definition')
        need(set(f)<= {'name','description','parameters','strict'}, 'unsupported function fields')
        need('strict' not in f or type(f['strict']) is bool, 'invalid function strict flag')
        validate_schema(f['parameters'])
        if anth and 'description' in t: f['description']=t['description']
        need('description' not in f or isinstance(f['description'],str), 'invalid tool description')
        converted.append({'type':'function','function':f})
    need(len({t['function']['name'] for t in converted})==len(converted), 'duplicate tool names')
    if converted: out['tools']=converted
    if 'tool_choice' in b:
        choice=b['tool_choice']
        if anth:
            need(isinstance(choice,dict) and not(set(choice)-{'type','name','disable_parallel_tool_use'}), 'invalid tool choice')
            typ=choice.get('type')
            need(typ in ('auto','any','none','tool'), 'invalid tool choice')
            if 'disable_parallel_tool_use' in choice:
                need(type(choice['disable_parallel_tool_use']) is bool, 'invalid parallel choice')
                out['parallel_tool_calls']=not choice['disable_parallel_tool_use']
            choice={'type':'function','function':{'name':choice.get('name')}} if typ=='tool' else {'auto':'auto','any':'required','none':'none'}[typ]
        need(choice in ('auto','required','none') if isinstance(choice,str) else isinstance(choice,dict) and choice.get('type')=='function' and isinstance(choice.get('function'),dict), 'invalid tool choice')
        names={t['function']['name'] for t in converted}
        need(choice in ('none','auto') or bool(names), 'tool choice without tools')
        if isinstance(choice,dict):
            need(set(choice)<= {'type','function'} and set(choice['function'])=={'name'}, 'unsupported tool choice fields')
            need(isinstance(choice['function'].get('name'),str) and choice['function']['name'] in names, 'undeclared chosen tool')
        out['tool_choice']=choice
    stop=b.get('stop_sequences' if anth else 'stop')
    if stop is not None:
        need(not anth and isinstance(stop,str) or isinstance(stop,list) and all(isinstance(s,str) for s in stop), 'invalid stop')
        out['stop']=stop
    if 'response_format' in b:
        rf=b['response_format']
        need(isinstance(rf,dict) and rf.get('type') in ('text','json_object','json_schema'), 'invalid response_format')
        need(set(rf)<= ({'type','json_schema'} if rf['type']=='json_schema' else {'type'}), 'unsupported response format fields')
        if rf['type']=='json_schema':
            need(isinstance(rf.get('json_schema'),dict) and isinstance(rf['json_schema'].get('schema'),dict) and isinstance(rf['json_schema'].get('name'),str), 'invalid json_schema')
            need('strict' not in rf['json_schema'] or type(rf['json_schema']['strict']) is bool, 'invalid schema strict flag')
            need(set(rf['json_schema'])<= {'name','schema','description','strict'}, 'unsupported JSON schema fields')
            need('description' not in rf['json_schema'] or isinstance(rf['json_schema']['description'],str), 'invalid schema description')
            validate_schema(rf['json_schema']['schema'])
        out['response_format']=rf
    pending=set()
    for m in out['messages']:
        if m['role']=='tool':
            need(m.get('tool_call_id') in pending, 'unmatched tool result')
            pending.remove(m['tool_call_id'])
        else:
            need(not pending, 'tool results must precede the next turn')
        if m.get('tool_calls'):
            validate_calls(m['tool_calls'],None)
            pending.update(c['id'] for c in m['tool_calls'])
        need(m.get('content') is not None or bool(m.get('tool_calls')), 'empty assistant turn')
    need(not pending, 'unresolved tool calls')
    return out


def validate_calls(calls, names):
    need(isinstance(calls,list), 'invalid tool_calls')
    ids=set()
    for c in calls:
        need(isinstance(c,dict) and isinstance(c.get('id'),str) and c['id'] and c['id'] not in ids and c.get('type')=='function', 'invalid tool call')
        ids.add(c['id']); f=c.get('function')
        need(isinstance(f,dict) and isinstance(f.get('name'),str) and isinstance(f.get('arguments'),str), 'invalid function call')
        need(names is None or f['name'] in names, 'undeclared provider tool')
        need(isinstance(loads(f['arguments']),dict), 'tool arguments must be object')


def validate_schema(s):
    """Validate supported schema syntax before spending an upstream call."""
    supported={'type','properties','required','additionalProperties','items','enum','const','description','title','$schema','minimum','maximum','minItems','maxItems','minLength','maxLength','pattern','anyOf','oneOf','allOf'}
    need(isinstance(s,dict) and not(set(s)-supported), 'unsupported schema fields')
    if 'type' in s:
        ts=s['type'] if isinstance(s['type'],list) else [s['type']]
        need(bool(ts) and all(isinstance(t,str) and t in ('object','array','string','integer','number','boolean','null') for t in ts), 'invalid schema type')
    if 'properties' in s:
        need(isinstance(s['properties'],dict), 'invalid schema properties')
        for child in s['properties'].values(): validate_schema(child)
    if 'required' in s:
        need(isinstance(s['required'],list) and all(isinstance(k,str) for k in s['required']), 'invalid schema required')
    if 'additionalProperties' in s:
        need(type(s['additionalProperties']) is bool or isinstance(s['additionalProperties'],dict), 'invalid additionalProperties')
        if isinstance(s['additionalProperties'],dict): validate_schema(s['additionalProperties'])
    if 'items' in s: validate_schema(s['items'])
    for k in ('anyOf','oneOf','allOf'):
        if k in s:
            need(isinstance(s[k],list) and bool(s[k]), 'invalid schema alternatives')
            for child in s[k]: validate_schema(child)
    for k in ('minItems','maxItems','minLength','maxLength'):
        if k in s: need(type(s[k]) is int and s[k]>=0, 'invalid schema bound')
    for k in ('minimum','maximum'):
        if k in s: need(type(s[k]) in (int,float) and math.isfinite(s[k]), 'invalid numeric schema bound')
    if 'enum' in s: need(isinstance(s['enum'],list) and bool(s['enum']), 'invalid schema enum')
    if 'pattern' in s:
        need(isinstance(s['pattern'],str), 'invalid schema pattern')
        try: re.compile(s['pattern'])
        except re.error: raise Invalid('invalid schema pattern')


def schema_valid(x,s):
    # Common JSON Schema constraints; unknown validation keywords fail closed.
    supported={'type','properties','required','additionalProperties','items','enum','const','description','title','$schema','minimum','maximum','minItems','maxItems','minLength','maxLength','pattern','anyOf','oneOf','allOf'}
    if set(s)-supported: return False
    types={'object':dict,'array':list,'string':str,'integer':int,'number':(int,float),'boolean':bool,'null':type(None)}
    if 'type' in s:
        ts=s['type'] if isinstance(s['type'],list) else [s['type']]
        if not any(t in types and isinstance(x,types[t]) and not(t in ('number','integer') and isinstance(x,bool)) for t in ts): return False
    if 'enum' in s and not any(dumps(x)==dumps(v) for v in s['enum']): return False
    if 'const' in s and dumps(x)!=dumps(s['const']): return False
    for k in ('allOf','anyOf','oneOf'):
        if k in s:
            n=sum(schema_valid(x,v) for v in s[k])
            if not (n==len(s[k]) if k=='allOf' else n>=1 if k=='anyOf' else n==1): return False
    if isinstance(x,dict):
        if any(k not in x for k in s.get('required',[])): return False
        props=s.get('properties',{})
        for k,v in x.items():
            spec=props.get(k,s.get('additionalProperties',True))
            if spec is False or isinstance(spec,dict) and not schema_valid(v,spec): return False
    if isinstance(x,list):
        if len(x)<s.get('minItems',0) or len(x)>s.get('maxItems',LIMIT): return False
        if isinstance(s.get('items'),dict) and not all(schema_valid(v,s['items']) for v in x): return False
    if isinstance(x,str):
        if len(x)<s.get('minLength',0) or len(x)>s.get('maxLength',LIMIT): return False
        if 'pattern' in s and not re.search(s['pattern'],x): return False
    if type(x) in (int,float) and not(s.get('minimum',-math.inf)<=x<=s.get('maximum',math.inf)): return False
    return True


def answer(raw, req, model):
    need(isinstance(raw,dict) and isinstance(raw.get('choices'),list) and len(raw['choices'])==1, 'invalid upstream choices')
    need('error' not in raw, 'provider returned an error')
    need('model' not in raw or raw['model'] in (model,model.removeprefix('openrouter/')), 'provider model mismatch')
    need(isinstance(raw['choices'][0],dict), 'invalid upstream choice')
    c=raw['choices'][0]; m=c.get('message',{}); reason=c.get('finish_reason')
    need(isinstance(m,dict) and not m.get('refusal'), 'invalid upstream message')
    need(c.get('index',0)==0, 'invalid choice index')
    need(reason in ('stop','length','tool_calls','content_filter'), 'unsupported upstream stop reason')
    need(m.get('role')=='assistant', 'invalid upstream role')
    txt=m.get('content')
    need(txt is None or isinstance(txt,str), 'invalid upstream text')
    calls=m.get('tool_calls',[])
    defs={t['function']['name']:t['function']['parameters'] for t in req.get('tools',[])}
    validate_calls(calls,set(defs))
    for call in calls:
        f=call['function']; need(schema_valid(loads(f['arguments']),defs[f['name']]), 'invalid tool input schema')
    need(bool(txt and txt.strip()) or bool(calls), 'empty upstream answer')
    need((reason=='tool_calls')==bool(calls), 'inconsistent tool finish reason')
    choice=req.get('tool_choice','auto')
    need(choice!='none' or not calls, 'forbidden tool call')
    need(choice!='required' or bool(calls), 'required tool missing')
    if isinstance(choice,dict): need(bool(calls) and all(c['function']['name']==choice['function']['name'] for c in calls), 'chosen tool missing')
    need(req.get('parallel_tool_calls',True) or len(calls)<=1, 'parallel tool calls forbidden')
    rf=req.get('response_format',{})
    if rf.get('type') in ('json_object','json_schema'):
        parsed=loads(txt or '')
        need(isinstance(parsed,dict) if rf['type']=='json_object' else schema_valid(parsed,rf['json_schema']['schema']), 'invalid JSON output')
    # Extract unique instruction markers, never count an echoed request as provider output.
    # Prior turns may contain different tokens; validate the current user instruction.
    current=next((m.get('content') for m in reversed(req['messages']) if m['role']=='user'),'')
    current=current if isinstance(current,str) else '\n'.join(b['text'] for b in current or [] if b['type']=='text')
    tokens=set(re.findall(r'\b[A-Z][A-Z0-9_]{0,63}-[0-9]{3,16}\b|\{\{[A-Za-z0-9_]+\}\}',current))
    body=(txt or '')+'\n'+'\n'.join(c['function']['arguments'] for c in calls)
    need(all(t in body for t in tokens), 'provider answer missing request token')
    u=raw.get('usage',{})
    need(isinstance(u,dict), 'invalid upstream usage')
    pi=u.get('prompt_tokens'); co=u.get('completion_tokens')
    if pi is None: pi=(len(dumps(req).encode())+2)//3
    if co is None: co=max(1,(len(body.encode())+2)//3)
    need(type(pi) is int and pi>=0 and type(co) is int and co>=1, 'invalid upstream usage counts')
    need('total_tokens' not in u or type(u['total_tokens']) is int and u['total_tokens']==pi+co, 'inconsistent upstream usage')
    msg={'role':'assistant','content':txt}
    if calls: msg['tool_calls']=calls
    ident='chatcmpl-'+hashlib.sha256(dumps(req).encode()).hexdigest()[:24]
    return {'id':ident,'object':'chat.completion','created':0,'model':model,'choices':[{'index':0,'message':msg,'finish_reason':reason}],'usage':{'prompt_tokens':pi,'completion_tokens':co,'total_tokens':pi+co}}


class Upstream(Exception):
    def __init__(self,status,code,failures):
        self.status=status; self.code=code; self.failures=failures


def exchange(url,body=None,headers=None,timeout=3):
    r=urllib.request.Request(url,data=None if body is None else body.encode(),headers=headers or {},method='GET' if body is None else 'POST')
    try: res=OPENER.open(r,timeout=timeout)
    except urllib.error.HTTPError as e: res=e
    with res:
        data=res.read(LIMIT+1)
        if len(data)>LIMIT: raise ValueError('upstream response too large')
        try: obj=loads(data)
        except (ValueError,UnicodeError): obj={}
        return res.status,obj


def complete(req, anth=False):
    vision=any(isinstance(m.get('content'),list) and any(b.get('type')=='image_url' for b in m['content']) for m in req['messages'])
    js=req.get('response_format',{}).get('type') in ('json_object','json_schema')
    estimated=len(dumps(req).encode())+req['max_tokens'] # conservative upper bound for text
    eligible=sorted((m for m in MODELS if (not js or m['json']) and (not vision or m['vision']) and estimated<=m['context']),key=lambda m:(-m['rating'],m['id']))
    need(bool(eligible), 'no model fits requested capabilities and context')
    failures=[]; attempts=0; last=(502,'invalid_upstream_result')
    for model in eligible:
        for retry in range(2):
            if attempts>=8: raise Upstream(*last,failures)
            attempts+=1
            call={**req,'model':model['id']}
            try:
                status,raw=exchange(os.environ['OPENAI_BASE_URL'].rstrip('/')+'/chat/completions',dumps(call),{'Content-Type':'application/json','Authorization':'Bearer '+os.environ['OPENAI_API_KEY']},timeout=10 if attempts==1 else 4 if attempts==2 else 2)
            except (TimeoutError,socket.timeout,urllib.error.URLError,OSError):
                raise Upstream(504,'upstream_timeout',failures)
            except ValueError:
                last=(502,'invalid_upstream_result'); break
            if status>=400:
                err=raw.get('error',{}) if isinstance(raw,dict) else {}
                code=err.get('code') or err.get('type') or 'http_'+str(status) if isinstance(err,dict) else 'http_'+str(status)
                code=str(code)[:160]
                code=re.sub(r'[^A-Za-z0-9_.:-]','_',code)
                last=(status,code)
                if status==429 or status>=500:
                    failures.append({'status':status,'code':code,'model':model['id']})
                    if code in ('request_cap','budget_exhausted'): raise Upstream(status,code,failures)
                    if retry==0: continue
                    break
                raise Upstream(status,code,failures)
            try:
                result=answer(raw,req,model['id'])
                if anth: anthropic(result)
                return result,failures
            except (ValueError,TypeError,KeyError,IndexError,RecursionError):
                last=(502,'invalid_upstream_result'); break
    raise Upstream(*last,failures)


def anthropic(a):
    c=a['choices'][0]; m=c['message']; blocks=[]
    need(c['finish_reason']!='content_filter', 'unsupported Messages content_filter')
    if m.get('content'): blocks.append({'type':'text','text':m['content']})
    for t in m.get('tool_calls',[]):
        blocks.append({'type':'tool_use','id':t['id'],'name':t['function']['name'],'input':loads(t['function']['arguments'])})
    return {'id':a['id'].replace('chatcmpl-','msg_'),'type':'message','role':'assistant','model':a['model'],'content':blocks,'stop_reason':{'stop':'end_turn','length':'max_tokens','tool_calls':'tool_use'}[c['finish_reason']],'stop_sequence':None,'usage':{'input_tokens':a['usage']['prompt_tokens'],'output_tokens':a['usage']['completion_tokens']}}


def stream_openai(a,include_usage):
    base={k:a[k] for k in ('id','created','model')}; base['object']='chat.completion.chunk'
    def chunk(delta,reason=None):
        return 'data: '+dumps({**base,'choices':[{'index':0,'delta':delta,'finish_reason':reason}]})+'\n\n'
    result=chunk({'role':'assistant'})
    m=a['choices'][0]['message']
    if m.get('content') is not None: result+=chunk({'content':m['content']})
    for i,t in enumerate(m.get('tool_calls',[])): result+=chunk({'tool_calls':[{'index':i,**t}]})
    result+=chunk({},a['choices'][0]['finish_reason'])
    if include_usage: result+='data: '+dumps({**base,'choices':[],'usage':a['usage']})+'\n\n'
    return result+'data: [DONE]\n\n'


def stream_anthropic(a):
    def event(typ,**data): return 'event: '+typ+'\ndata: '+dumps({'type':typ,**data})+'\n\n'
    start={**a,'content':[],'stop_reason':None,'stop_sequence':None,'usage':{**a['usage'],'output_tokens':0}}
    result=event('message_start',message=start)
    for i,b in enumerate(a['content']):
        initial={'type':'text','text':''} if b['type']=='text' else {**b,'input':{}}
        result+=event('content_block_start',index=i,content_block=initial)
        delta={'type':'text_delta','text':b['text']} if b['type']=='text' else {'type':'input_json_delta','partial_json':dumps(b['input'])}
        result+=event('content_block_delta',index=i,delta=delta)
        result+=event('content_block_stop',index=i)
    return result+event('message_delta',delta={'stop_reason':a['stop_reason'],'stop_sequence':a['stop_sequence']},usage={'output_tokens':a['usage']['output_tokens']})+event('message_stop')


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def setup(self):
        super().setup(); self.connection.settimeout(5)
    def send(self,status,obj,sse=False,failures=()):
        raw=(obj if sse else dumps(obj)).encode()
        if len(raw)>LIMIT: raise Invalid('response too large')
        self.send_response_only(status); self.send_header('Content-Type','text/event-stream; charset=utf-8' if sse else 'application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        if failures: self.send_header('x-makefun-upstream-failures',dumps(failures))
        self.end_headers(); self.wfile.write(raw)
    def error(self,status,msg,code='invalid_request',failures=()):
        typ='authentication_error' if status==401 else 'invalid_request_error' if status<500 else 'api_error'
        anth=self.path=='/v1/messages' or self.path=='/v1/models' and 'x-api-key' in self.headers
        obj={'type':'error','error':{'type':typ,'message':msg}} if anth else {'error':{'message':msg,'type':typ,'param':None,'code':code}}
        self.send(status,obj,failures=failures)
    def auth(self):
        oa=self.headers.get('Authorization'); an=self.headers.get('x-api-key')
        if oa is not None and an is not None: return 400
        expected=os.environ.get('AGGREGATOR_CLIENT_KEY',CLIENT_KEY)
        if oa is not None:
            return 0 if expected and hmac.compare_digest(oa.encode(),('Bearer '+expected).encode()) else 401
        return 0 if an and expected and hmac.compare_digest(an.encode(),expected.encode()) else 401
    def handle_request(self):
        try:
            if self.path not in ('/v1/models','/v1/chat/completions','/v1/messages'): return self.error(404,'unknown path','not_found')
            if self.command!=('GET' if self.path=='/v1/models' else 'POST'): return self.error(405,'unsupported method','method_not_allowed')
            status=self.auth()
            if status: return self.error(status,'ambiguous authentication headers' if status==400 else 'invalid credentials')
            if self.path=='/v1/models':
                ids=[m['id'] for m in MODELS]
                if 'Authorization' in self.headers: obj={'object':'list','data':[{'id':i,'object':'model','created':0,'owned_by':'openrouter'} for i in ids]}
                else: obj={'data':[{'id':i,'type':'model','display_name':i,'created_at':'1970-01-01T00:00:00Z'} for i in ids],'has_more':False,'first_id':ids[0],'last_id':ids[-1]}
                return self.send(200,obj)
            need(not self.headers.get('Transfer-Encoding'), 'unsupported transfer encoding')
            need(self.headers.get_content_type()=='application/json', 'expected application/json')
            lengths=self.headers.get_all('Content-Length',[])
            need(len(lengths)==1 and lengths[0].isdigit(), 'one Content-Length is required')
            length=int(lengths[0]); need(0<length<=LIMIT,'invalid request size')
            data=self.rfile.read(length); need(len(data)==length,'incomplete body')
            b=loads(data); anth=self.path=='/v1/messages'
            req=translate(b,anth)
            # Messages cannot represent content_filter; reject before downstream success.
            a,failures=complete(req,anth)
            try: obj=anthropic(a) if anth else a
            except Invalid: raise Upstream(502,'invalid_upstream_result',failures)
            sse=b.get('stream',False)
            if sse: obj=stream_anthropic(obj) if anth else stream_openai(obj,b.get('stream_options',{}).get('include_usage',False))
            if len((obj if sse else dumps(obj)).encode())>LIMIT:
                raise Upstream(502,'upstream_response_too_large',failures)
            self.send(200,obj,sse,failures)
        except Upstream as e: self.error(e.status,'gateway completion failed: '+e.code,e.code,e.failures)
        except (ValueError,TypeError,KeyError,UnicodeError,RecursionError): self.error(400,'invalid or unsupported request')
        except (BrokenPipeError,ConnectionResetError,socket.timeout): pass
        except Exception:
            self.error(502,'upstream exchange failed','upstream_error')
    do_GET=handle_request
    do_POST=handle_request
    do_PUT=handle_request
    do_DELETE=handle_request
    do_PATCH=handle_request
    do_OPTIONS=handle_request
    do_HEAD=handle_request


def main():
    if sys.argv[1:]==['--list']:
        sys.stdout.write(CATALOG_TEXT); return
    if sys.argv[1:]: raise SystemExit('usage: python3 main.py [--list]')
    for k in ('MAKEFUN_ENV_URL','OPENAI_BASE_URL','OPENAI_API_KEY','ANTHROPIC_API_KEY'):
        if not os.environ.get(k): raise SystemExit('missing '+k)
    server=ThreadingHTTPServer(('0.0.0.0',0),Handler)
    server.daemon_threads=True
    worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
    base=os.environ['MAKEFUN_ENV_URL'].rstrip('/')
    try:
        status,obj=exchange(base+'/register','{"protocol_version":1,"port":'+str(server.server_port)+',"catalog":'+CATALOG_TEXT+'}',{'Content-Type':'application/json'})
        if status!=200 or obj.get('ok') is not True: raise RuntimeError('registration refused')
        print('registered',file=sys.stderr)
        stop=threading.Event()
        while True:
            status,obj=exchange(base+'/work')
            if status!=200 or type(obj.get('done')) is not bool: raise RuntimeError('invalid work response')
            if obj['done']: break
            stop.wait(.25)
    finally:
        server.shutdown(); server.server_close(); worker.join()


if __name__=='__main__':
    try: main()
    except Exception as e:
        print('server failed: '+type(e).__name__,file=sys.stderr); sys.exit(1)
