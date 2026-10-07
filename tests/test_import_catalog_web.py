import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
from model_catalog import Catalog
from import_worker import document
from web_sources import checked_url,PageText

class ImportCatalogTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def test_gguf_registration_and_location_persist_but_active_is_protected(self):
        path=self.root/'model.gguf';path.write_bytes(b'GGUF'+b'test')
        application=app.Application(self.root/'data');catalog=application.engines.catalog
        identifier=catalog.save({'path':str(path),'label':'My model','context_length':4096})['id']
        self.assertEqual(Catalog(self.root/'data').items[identifier]['path'],str(path))
        config=application.engines.configs()[identifier]
        from engines import profile_for
        self.assertEqual(profile_for(config),identifier)
        with self.assertRaises(ValueError): catalog.save({'id':identifier,'delete':True},identifier)
        with self.assertRaises(app.AppError): application.save_settings(dict(config,context_length=8192))
        bad=self.root/'bad.gguf';bad.write_text('not a model')
        with self.assertRaises(ValueError):catalog.save({'path':str(bad),'label':'bad'})
    def test_docx_body_and_table_text_are_extracted_without_external_links(self):
        path=self.root/'fixture.docx'
        with zipfile.ZipFile(path,'w') as package:
            package.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>会議の日時</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>10:00</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        result=document(path);self.assertIn('会議の日時',result['text']);self.assertIn('10:00',result['text']);self.assertTrue(result['local'])
    def test_reserved_windows_creation_names_are_rejected(self):
        for name in ['CON.py','nul.txt','folder /a.py','COM1.py','end./a.py']:
            with self.subTest(name=name),self.assertRaises(app.AppError):app.safe_create(self.root,name)

class PublicWebTests(unittest.TestCase):
    def test_private_addresses_and_unsafe_schemes_never_connect(self):
        for url in ['http://example.com','https://user:pass@example.com','https://example.com:8765']:
            with self.subTest(url=url),self.assertRaises(ValueError):checked_url(url)
        for address in ['127.0.0.1','10.1.1.1','169.254.169.254','::1','::ffff:127.0.0.1']:
            with self.subTest(address=address),patch('web_sources.socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))]),self.assertRaises(ValueError):checked_url('https://example.com')
    def test_public_connection_uses_the_checked_address_and_html_has_no_script(self):
        with patch('web_sources.socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))]):
            parsed,address=checked_url('https://example.com/page');self.assertEqual(address[-1][0],'8.8.8.8')
        parser=PageText();parser.feed('<title>Title</title><script>evil()</script><p>Source text</p>')
        self.assertNotIn('evil',''.join(parser.text));self.assertIn('Source text',''.join(parser.text))

if __name__=='__main__':unittest.main()
