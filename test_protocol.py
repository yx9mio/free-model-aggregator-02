"""Controlled fixtures only; these are never used by the submitted server."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import main as app

TOKEN='ZEBRA-4721'
REQ={'model':'auto','messages':[{'role':'user','content':'Return '+TOKEN}],'max_tokens':100}


def provider(text=TOKEN,calls=None):
    m={'role':'assistant','content':text}
    if calls: m['tool_calls']=calls
    return {'choices':[{'message':m,'finish_reason':'tool_calls' if calls else 'stop'}],'usage':{'prompt_tokens':12,'completion_tokens':7}}


class ProtocolTests(unittest.TestCase):
    def test_ranking_and_cap(self):
        for jsonmode,expected in [(False,0),(True,1)]:
            b={**REQ,'max_tokens':99999}
            if jsonmode: b['response_format']={'type':'json_object'}
            req=app.translate(b,False)
            seen=[]
            def exchange(url,body,headers,**kwargs):
                seen.append(json.loads(body)); return 200,provider(json.dumps({'token':TOKEN}) if jsonmode else TOKEN)
            with patch.dict(os.environ,OPENAI_BASE_URL='http://fixture/v1/',OPENAI_API_KEY='fixture'),patch.object(app,'exchange',exchange):
                answer,failures=app.complete(req)
            self.assertEqual(len(seen),1)
            self.assertEqual(answer['model'],app.MODELS[expected]['id'])
            self.assertEqual(seen[0]['max_tokens'],8192)
            self.assertFalse(failures)

    def test_failover_and_provenance(self):
        req=app.translate(REQ,False); seen=[]
        replies=[(429,{'error':{'code':'rate_limit_exceeded'}}),(503,{}),(200,provider('missing')),(200,provider())]
        def exchange(url,body,headers,**kwargs):
            seen.append(json.loads(body)['model']); return replies.pop(0)
        with patch.dict(os.environ,OPENAI_BASE_URL='http://fixture/v1',OPENAI_API_KEY='fixture'),patch.object(app,'exchange',exchange):
            a,trail=app.complete(req)
        self.assertEqual(seen,[app.MODELS[i]['id'] for i in (0,0,1,2)])
        self.assertEqual(a['model'],app.MODELS[2]['id'])
        self.assertEqual([r['code'] for r in trail],['rate_limit_exceeded','http_503'])
        self.assertEqual([r['status'] for r in trail],[429,503])

    def test_attempt_limit_and_terminal(self):
        req=app.translate(REQ,False)
        with patch.dict(os.environ,OPENAI_BASE_URL='http://fixture',OPENAI_API_KEY='fixture'):
            with patch.object(app,'exchange',return_value=(503,{})) as call:
                with self.assertRaises(app.Upstream) as caught: app.complete(req)
                self.assertEqual(call.call_count,8); self.assertEqual(len(caught.exception.failures),8)
            with patch.object(app,'exchange',return_value=(429,{'error':{'code':'request_cap'}})) as call:
                with self.assertRaises(app.Upstream): app.complete(req)
                self.assertEqual(call.call_count,1)
            with patch.object(app,'exchange',return_value=(401,{})) as call:
                with self.assertRaises(app.Upstream): app.complete(req)
                self.assertEqual(call.call_count,1)

    def test_no_reasoning_or_metadata_token_credit(self):
        req=app.translate(REQ,False)
        p=provider('wrong'); p['token']=TOKEN; p['choices'][0]['message']['reasoning']=TOKEN
        with self.assertRaises(app.Invalid): app.answer(p,req,app.MODELS[0]['id'])
        p=provider('not JSON '+TOKEN); req['response_format']={'type':'json_object'}
        with self.assertRaises(ValueError): app.answer(p,req,app.MODELS[1]['id'])

    def test_tool_translation_validation_and_stream(self):
        b={'model':'auto','max_tokens':200,'system':[{'type':'text','text':'Keep this'}],
           'messages':[{'role':'user','content':'Return '+TOKEN}],
           'tools':[{'name':'lookup','input_schema':{'type':'object','properties':{'token':{'type':'string'}},'required':['token'],'additionalProperties':False}}],
           'tool_choice':{'type':'tool','name':'lookup'},'stop_sequences':['END']}
        req=app.translate(b,True)
        self.assertEqual(req['messages'][0],{'role':'system','content':'Keep this'})
        self.assertEqual(req['stop'],['END'])
        calls=[{'id':'tool_1','type':'function','function':{'name':'lookup','arguments':json.dumps({'token':TOKEN})}}]
        a=app.answer(provider(None,calls),req,app.MODELS[0]['id']); msg=app.anthropic(a)
        s=app.stream_anthropic(msg)
        events=[json.loads(frame.split('data: ')[1]) for frame in s.strip().split('\n\n')]
        self.assertEqual([e['type'] for e in events],['message_start','content_block_start','content_block_delta','content_block_stop','message_delta','message_stop'])
        self.assertEqual(json.loads(events[2]['delta']['partial_json']),{'token':TOKEN})
        self.assertNotIn('[DONE]',s)
        calls[0]['function']['name']='undeclared'
        with self.assertRaises(app.Invalid): app.answer(provider(None,calls),req,app.MODELS[0]['id'])

    def test_openai_stream(self):
        a=app.answer(provider(),app.translate(REQ,False),app.MODELS[0]['id'])
        frames=app.stream_openai(a,True).strip().split('\n\n')
        self.assertEqual(frames[-1],'data: [DONE]')
        chunks=[json.loads(f[6:]) for f in frames[:-1]]
        self.assertEqual(chunks[0]['choices'][0]['delta'],{'role':'assistant'})
        self.assertEqual(chunks[-2]['choices'][0]['finish_reason'],'stop')
        self.assertEqual(chunks[-1]['choices'],[])
        self.assertTrue(all(c['id']==a['id'] and c['model']==a['model'] for c in chunks))

    def test_bad_requests_no_gateway(self):
        for update in ({'model':'other'},{'max_tokens':True},{'stream':'yes'},{'tools':{}},{'top_k':5},{'messages':[{'role':'user','content':[{'type':'audio'}]}]}):
            with self.assertRaises(app.Invalid): app.translate({**REQ,**update},False)

    def test_catalog_bytes(self):
        raw=subprocess.check_output([sys.executable,'main.py','--list'])
        self.assertEqual(raw,app.CATALOG_TEXT.encode()); self.assertFalse(raw.endswith(b'\n'))
        self.assertEqual(len({m['id'] for m in app.MODELS}),14)

    def test_current_token_and_placeholder_provenance(self):
        req=app.translate({**REQ,'messages':[{'role':'user','content':'Return OLD-1234'},
                                            {'role':'assistant','content':'OLD-1234'},
                                            {'role':'user','content':'Return '+TOKEN}]},False)
        self.assertEqual(app.answer(provider(),req,app.MODELS[0]['id'])['choices'][0]['message']['content'],TOKEN)
        marker='{{UNIQUE_EXAMPLE}}'
        req=app.translate({**REQ,'messages':[{'role':'user','content':'Return '+marker}]},False)
        with self.assertRaises(app.Invalid): app.answer(provider('missing'),req,app.MODELS[0]['id'])
        app.answer(provider(marker),req,app.MODELS[0]['id'])

    def test_malformed_upstream_falls_back(self):
        req=app.translate(REQ,False)
        responses=[(200,{'choices':[None]}),(200,provider())]
        with patch.dict(os.environ,OPENAI_BASE_URL='http://fixture',OPENAI_API_KEY='fixture'),patch.object(app,'exchange',side_effect=lambda *a,**k:responses.pop(0)) as call:
            a,trail=app.complete(req)
        self.assertEqual(call.call_count,2)
        self.assertEqual(a['model'],app.MODELS[1]['id']); self.assertEqual(trail,[])
        bad=provider(); bad['usage']['completion_tokens']=0
        with self.assertRaises(app.Invalid): app.answer(bad,req,app.MODELS[0]['id'])

    def test_schema_and_semantics_rejected_before_gateway(self):
        cases=[{'response_format':{'type':'json_object','unexpected':True}},
               {'messages':[{'role':'user','content':[{'type':'text','text':TOKEN,'cache_control':{}}]}]},
               {'messages':[{'role':'tool','content':'result','tool_call_id':'missing'}]},
               {'tools':[{'type':'function','function':{'name':'lookup','parameters':{'$ref':'outside'}}}]},
               {'response_format':{'type':'json_schema','json_schema':{'name':'x','schema':{'type':'object','required':7}}}}]
        for update in cases:
            with self.subTest(update=update),self.assertRaises(app.Invalid): app.translate({**REQ,**update},False)
        with self.assertRaises(app.Invalid): app.loads('{"model":"auto","model":"auto"}')

    def test_lifecycle_and_http_replay(self):
        exports=[]
        for run in range(2):
            records=[]; registered=[]
            class Fixture(BaseHTTPRequestHandler):
                def log_message(self,*args): pass
                def send(self,obj):
                    raw=app.dumps(obj).encode(); self.send_response(200); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
                def do_POST(self):
                    b=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    if self.path=='/register': registered.append(b); self.send({'ok':True,'requests':6})
                    elif self.path=='/v1/chat/completions':
                        records.append(b); self.send(provider(json.dumps({'token':TOKEN}) if 'response_format' in b else TOKEN))
                    else: self.send_error(400)
                def do_GET(self):
                    self.send({'done':bool(registered) and len(results)==8})
            results=[]
            f=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
            thread=threading.Thread(target=f.serve_forever,daemon=True); thread.start()
            origin='http://127.0.0.1:'+str(f.server_port)
            env={**os.environ,'MAKEFUN_ENV_URL':origin,'OPENAI_BASE_URL':origin+'/v1','OPENAI_API_KEY':'fixture','ANTHROPIC_API_KEY':'fixture'}
            p=subprocess.Popen([sys.executable,'main.py'],env=env,stderr=subprocess.PIPE)
            try:
                import time
                for _ in range(100):
                    if registered: break
                    time.sleep(.02)
                self.assertTrue(registered)
                base='http://127.0.0.1:'+str(registered[0]['port'])
                for path,body,headers,method,expected in [
                    ('/v1/models',None,{},'GET',401),
                    ('/v1/models',None,{'Authorization':'Bearer wrong'},'GET',401),
                    ('/v1/models',None,{'Authorization':'Bearer makefun-client','x-api-key':'makefun-client'},'GET',400),
                    ('/missing',None,{},'GET',404),
                    ('/v1/messages',None,{'x-api-key':'makefun-client'},'GET',405),
                    ('/v1/messages',b'{',{'x-api-key':'makefun-client','Content-Type':'application/json'},'POST',400),
                ]:
                    request=app.urllib.request.Request(base+path,data=body,headers=headers,method=method)
                    with self.assertRaises(app.urllib.error.HTTPError) as caught:
                        app.OPENER.open(request,timeout=5)
                    self.assertEqual(caught.exception.code,expected)
                    error=json.loads(caught.exception.read()); caught.exception.close()
                    self.assertIn('error',error)
                    if path=='/v1/messages': self.assertEqual(error['type'],'error')
                self.assertFalse(records)
                for headers in ({'Authorization':'Bearer makefun-client'},{'x-api-key':'makefun-client'}):
                    status,listing=app.exchange(base+'/v1/models',headers=headers)
                    self.assertEqual(status,200)
                    self.assertEqual([r['id'] for r in listing['data']],[m['id'] for m in app.MODELS]); results.append(listing)
                for i in range(6):
                    anth=i in (2,4,5)
                    b=copy.deepcopy(REQ)
                    if anth and i==4: b['tools']=[{'name':'lookup','input_schema':{'type':'object'}}]
                    if i in (0,3): b['response_format']={'type':'json_object'}
                    if i in (1,2): b['stream']=True
                    url=base+('/v1/messages' if anth else '/v1/chat/completions')
                    r=app.OPENER.open(app.urllib.request.Request(url,data=app.dumps(b).encode(),headers={'Content-Type':'application/json',('x-api-key' if anth else 'Authorization'):('makefun-client' if anth else 'Bearer makefun-client')}),timeout=5)
                    raw=r.read().decode(); self.assertEqual(r.status,200); results.append(raw)
                self.assertEqual(p.wait(timeout=5),0)
                self.assertEqual(len(records),6)
                self.assertEqual([r['model'] for r in records],[app.MODELS[i]['id'] for i in (1,0,0,1,0,0)])
                self.assertEqual(registered[0]['catalog'],app.CATALOG)
                exports.append(app.dumps({'records':records,'results':results,'catalog':registered[0]['catalog']}))
            finally:
                if p.poll() is None: p.kill(); p.wait()
                p.stderr.close(); f.shutdown(); f.server_close(); thread.join()
        self.assertEqual(exports[0],exports[1])


if __name__=='__main__': unittest.main()
