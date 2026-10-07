"""Native bridge contracts, using fixtures without reading the user's clipboard."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
import urllib.request
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
from desktop import DesktopSession

class NativeIntegrationTests(unittest.TestCase):
    def test_companion_routes_are_authenticated_and_cancel_owned_active_request(self):
        import urllib.error
        with tempfile.TemporaryDirectory() as temporary:
            server=app.make_server(0,Path(temporary));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}';opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with self.assertRaises(urllib.error.HTTPError) as denied:opener.open(base+'/api/activity')
                self.assertEqual(denied.exception.code,403);denied.exception.close()
                def post(route,body,origin=None):
                    headers={'X-App-Token':server.application.token,'Content-Type':'application/json'}
                    if origin:headers['Origin']=origin
                    return opener.open(urllib.request.Request(base+route,data=json.dumps(body).encode(),headers=headers))
                server.application.desktop_controller=Mock()
                server.application.desktop_controller.action.return_value={'mode':'companion','expanded':True}
                with self.assertRaises(urllib.error.HTTPError) as denied:post('/api/desktop',{'action':'expand'},'https://evil.test')
                self.assertEqual(denied.exception.code,403);denied.exception.close();server.application.desktop_controller.action.assert_not_called()
                with post('/api/desktop',{'action':'expand'}) as response:self.assertTrue(json.load(response)['expanded'])
                cancelled=threading.Event();server.application.active={'fixture':{'cancel':cancelled}}
                with post('/api/cancel',{}) as response:self.assertTrue(json.load(response)['ok'])
                self.assertTrue(cancelled.is_set())
            finally:server.shutdown();thread.join(3);server.server_close();server.application.connections.close()

    def test_picker_and_clipboard_only_run_for_explicit_authenticated_requests(self):
        with tempfile.TemporaryDirectory() as temporary:
            server=app.make_server(0,Path(temporary)/'data')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with patch('productivity.clipboard_text',return_value='fixture clipboard') as clipboard:
                    with opener.open(base+'/api/bootstrap') as response: token=json.load(response)['token']
                    clipboard.assert_not_called()
                    def post(route,body):
                        request=urllib.request.Request(base+route,data=json.dumps(body).encode(),headers={'X-App-Token':token,'Content-Type':'application/json'})
                        with opener.open(request) as response: return json.load(response)
                    self.assertEqual(post('/api/clipboard',{})['text'],'fixture clipboard')
                    clipboard.assert_called_once()
                    window=Mock();window.create_file_dialog.return_value=[temporary]
                    server.application.desktop_window=window
                    fake=types.SimpleNamespace(FileDialog=types.SimpleNamespace(FOLDER=31,OPEN=32))
                    with patch.dict(sys.modules,{'webview':fake}):
                        self.assertEqual(post('/api/pick',{'kind':'folder'})['path'],temporary)
                    window.create_file_dialog.assert_called_once_with(31,allow_multiple=False)
            finally:
                server.shutdown();thread.join(3);server.server_close()

    def test_native_drop_passes_full_paths_as_escaped_data(self):
        server=Mock(server_port=8765);window=Mock()
        window.get_current_url.return_value='http://127.0.0.1:8765/'
        element=window.dom.get_element.return_value
        fake=types.SimpleNamespace(DOMEventHandler=lambda handler,**kwargs:types.SimpleNamespace(handler=handler,options=kwargs))
        with patch.dict(sys.modules,{'webview.dom':fake}): DesktopSession(server,window,'bonsai').bind_drop()
        event=element.on.call_args.args[1]
        self.assertTrue(event.options['prevent_default'])
        name='C:\\fixture\\quote"file.py'
        event.handler({'dataTransfer':{'files':[{'pywebviewFullPath':name},{}]}})
        code=window.evaluate_js.call_args.args[0]
        self.assertIn(json.dumps([name]),code)
        self.assertEqual(element.on.call_args.args[0],'drop')
