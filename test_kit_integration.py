"""Actual public kit environment, controlled gateway: NOT live provider evidence."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import main as app


KIT = Path(os.environ.get('MAKEFUN_KIT', '../free-model-aggregator-02-kit')).resolve()
ROOT = Path(__file__).resolve().parent


class KitIntegration(unittest.TestCase):
    @unittest.skipUnless((KIT/'program/env/server.mjs').exists(), 'public kit not installed')
    def test_all_public_workloads_with_exact_environment(self):
        calls=[]
        registration=[]
        class Gateway(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                assert self.path=='/v1/chat/completions'
                assert 0<body['max_tokens']<=8192 and body['stream'] is False
                assert self.headers['Authorization']=='Bearer fixture-gateway'
                calls.append(body)
                user=next(m['content'] for m in reversed(body['messages']) if m['role']=='user')
                tokens=re.findall(r'\b[A-Z][A-Z0-9_]{0,63}-[0-9]{3,16}\b|\{\{[A-Za-z0-9_]+\}\}',user)
                assert len(tokens)==1
                output=app.dumps({'token':tokens[0]}) if body.get('response_format',{}).get('type')=='json_object' else tokens[0]
                answer={'model':body['model'].removeprefix('openrouter/'),
                        'choices':[{'index':0,'message':{'role':'assistant','content':output},'finish_reason':'stop'}],
                        'usage':{'prompt_tokens':10,'completion_tokens':8,'total_tokens':18}}
                raw=app.dumps(answer).encode()
                self.send_response_only(200); self.send_header('Content-Length',str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
        gateway=ThreadingHTTPServer(('127.0.0.1',0),Gateway)
        worker=threading.Thread(target=gateway.serve_forever,daemon=True); worker.start()
        try:
            for trial in range(3):
                exports=[]; recorded=[]
                workload=json.loads((KIT/f'practice/trial-{trial}/input/env/workload.json').read_text())
                expected=[app.MODELS[1 if b.get('response_format',{}).get('type')=='json_object' else 0]['id']
                          for b in (workload['bodies'][p['id']] for p in workload['manifest'])]
                for repeat in range(2):
                    calls.clear()
                    with tempfile.TemporaryDirectory() as tmp:
                        env={**os.environ,'MAKEFUN_ENV_DATA_DIR':str(KIT/f'practice/trial-{trial}/input/env'),
                             'MAKEFUN_STATE_DIR':tmp,'MAKEFUN_ENV_PORT':'0'}
                        ep=subprocess.Popen(['node',str(KIT/'program/env/server.mjs')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                        try:
                            ready=Path(tmp)/'ready'
                            for _ in range(300):
                                if ready.exists(): break
                                if ep.poll() is not None: self.fail('Kit environment did not start')
                                time.sleep(.01)
                            self.assertTrue(ready.exists())
                            port=json.loads(ready.read_text())['port']
                            botenv={**os.environ,'MAKEFUN_ENV_URL':f'http://127.0.0.1:{port}',
                                    'OPENAI_BASE_URL':f'http://127.0.0.1:{gateway.server_port}/v1',
                                    'OPENAI_API_KEY':'fixture-gateway','ANTHROPIC_API_KEY':'fixture-gateway'}
                            botenv.pop('AGGREGATOR_CLIENT_KEY',None)
                            bot=subprocess.run([sys.executable,str(ROOT/'main.py')],env=botenv,timeout=20,capture_output=True)
                            self.assertEqual(bot.returncode,0,bot.stderr.decode())
                        finally:
                            ep.terminate(); ep.communicate(timeout=5)
                        self.assertEqual(len(calls),6)
                        self.assertEqual([c['model'] for c in calls],expected)
                        state=(Path(tmp)/'export/state.json').read_bytes()
                        observed=json.loads(state)
                        self.assertEqual(len(observed['prompts']),6)
                        self.assertTrue(all(p['valid'] and p['token_ok'] and p['status']==200
                                            and all(c['pass'] for c in p['checks']) for p in observed['prompts']),state.decode())
                        # Exact canonical state is exported by the kit, with operational ports excluded.
                        exports.append(state); recorded.append(app.dumps(calls))
                self.assertEqual(exports[0],exports[1],f'trial-{trial} state mismatch')
                self.assertEqual(recorded[0],recorded[1],f'trial-{trial} calls mismatch')
                print(f'controlled kit trial-{trial}: six calls per run; canonical exports byte-identical')
        finally:
            gateway.shutdown(); gateway.server_close(); worker.join()


if __name__=='__main__': unittest.main()
