"""Repair is bounded; cancellation interrupts waiting for upstream headers."""
import json
from pathlib import Path
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app

class Model(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(body)
        if self.server.stall:
            self.server.entered.set();self.server.released.wait(10)
            return
        index=min(len(self.server.requests)-1,len(self.server.outputs)-1)
        result=self.server.outputs[index]
        self.send_response(200);self.end_headers()
        raw={'choices':[{'delta':{'content':result},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}
        try: self.wfile.write(('data: '+json.dumps(raw)+'\n\ndata: [DONE]\n\n').encode())
        except ConnectionError: pass

class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.model=ThreadingHTTPServer(('127.0.0.1',0),Model);self.model.daemon_threads=True
        self.model.requests=[];self.model.outputs=['bad'];self.model.stall=False
        self.model.entered=threading.Event();self.model.released=threading.Event()
        self.server=app.make_server(0,self.root/'data');self.application=self.server.application
        self.application.save_settings({'endpoint':f'http://127.0.0.1:{self.model.server_port}/v1','model':'test'})
        self.threads=[]
        for server in [self.model,self.server]:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.threads.append(thread)
        self.conversation=self.application.store.create();self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.application.cancel_active();self.model.released.set()
        for server in [self.server,self.model]: server.shutdown();server.server_close()
        for thread in self.threads: thread.join(2)
        self.temp.cleanup()

    def request(self,body):
        request=urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}/api/chat',data=json.dumps(body).encode(),headers={'X-App-Token':self.application.token,'Content-Type':'application/json'})
        return self.opener.open(request,timeout=5)

    def edit(self):
        project=self.root/'project';project.mkdir();(project/'a.py').write_text('answer = 1\n')
        return {'conversation':self.conversation,'prompt':'fix','root':str(project),'files':['a.py'],'mode':'edit'}

    def test_bad_json_is_repaired_once_and_original_response_retained(self):
        self.model.outputs=['not JSON',json.dumps({'explanation':'fixed','edits':[{'path':'a.py','old':'answer = 1','new':'answer = 2'}]})]
        with self.request(self.edit()) as response: result=response.read().decode()
        metadata=self.application.store.messages(self.conversation)[-1]['metadata']
        self.assertEqual(len(self.model.requests),2);self.assertEqual(metadata['repair_attempts'],1)
        self.assertEqual(metadata['initial_output'],'not JSON');self.assertEqual(metadata['edits'][0]['new'],'answer = 2')
        self.assertEqual(metadata['usage']['total_tokens'],30);self.assertIn('event: reset',result)

    def test_invalid_repair_never_loops_and_never_modifies_files(self):
        body=self.edit()
        with self.request(body) as response: response.read()
        self.assertEqual(len(self.model.requests),2)
        metadata=self.application.store.messages(self.conversation)[-1]['metadata']
        self.assertEqual(metadata['edits'],[]);self.assertTrue(metadata['proposal_invalid'])
        self.assertEqual((Path(body['root'])/'a.py').read_text(),'answer = 1\n')

    def test_cancel_before_headers_finishes_promptly_and_keeps_history(self):
        self.model.stall=True
        response=self.request({'conversation':self.conversation,'prompt':'wait','mode':'chat','request_id':'waiting'})
        self.assertTrue(self.model.entered.wait(3));started=time.monotonic()
        self.application.cancel_active('waiting');response.read();response.close()
        self.assertLess(time.monotonic()-started,2)
        self.assertFalse(self.application.generation_lock.locked())
        self.assertTrue(self.application.store.messages(self.conversation)[-1]['metadata']['cancelled'])

if __name__=='__main__': unittest.main()
