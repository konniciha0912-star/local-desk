"""Opt-in public HTTPS sources. Never connect to private addresses or follow them."""
from datetime import datetime, timezone
import html
from html.parser import HTMLParser
import http.client
import ipaddress
import socket
import ssl
import urllib.parse
import xml.etree.ElementTree as ET
import zlib

MAX_RESPONSE=2*1024*1024

def checked_url(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443): raise ValueError("公開HTTPSページを選んでください。")
    addresses=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
    if not addresses: raise ValueError("ページの接続先が見つかりません。")
    for family,kind,protocol,canon,address in addresses:
        value=ipaddress.ip_address(address[0])
        if value.version==6 and value.ipv4_mapped: value=value.ipv4_mapped
        if not value.is_global: raise ValueError("PC内・プライベートネットワークのページは取得できません。")
    return parsed,addresses[0]

class PublicConnection(http.client.HTTPSConnection):
    def __init__(self,host,address):
        super().__init__(host,443,timeout=10,context=ssl.create_default_context());self.address=address
    def connect(self):
        family,kind,protocol,canon,address=self.address
        sock=socket.socket(family,kind,protocol);sock.settimeout(self.timeout)
        try:
            sock.connect(address);self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
        except Exception: sock.close();raise

def fetch(url):
    for _ in range(4):
        parsed,address=checked_url(url);connection=PublicConnection(parsed.hostname,address)
        try:
            target=urllib.parse.urlunsplit(("","",parsed.path or "/",parsed.query,""))
            connection.request("GET",target,headers={"User-Agent":"LocalDesk/2.0 (+explicit-user-request)","Accept":"text/html,application/rss+xml,text/plain","Accept-Encoding":"identity"})
            response=connection.getresponse()
            if response.status in {301,302,303,307,308}:
                location=response.getheader("Location")
                if not location: raise ValueError("転送先がありません。")
                url=urllib.parse.urljoin(url,location);continue
            if response.status!=200: raise ValueError(f"ページ取得に失敗しました（HTTP {response.status}）。")
            mime=response.getheader("Content-Type","")
            if not any(value in mime.lower() for value in ["text/","application/rss+xml","application/xml","application/xhtml+xml"]): raise ValueError("文字のWebページだけ取得できます。")
            raw=response.read(MAX_RESPONSE+1)
            if len(raw)>MAX_RESPONSE: raise ValueError("ページが2MBを超えています。")
            if response.getheader("Content-Encoding","").lower()=="gzip":
                decoder=zlib.decompressobj(31);raw=decoder.decompress(raw,MAX_RESPONSE+1)
                if len(raw)>MAX_RESPONSE or decoder.unconsumed_tail: raise ValueError("ページの展開サイズが大きすぎます。")
            charset=response.headers.get_content_charset() or "utf-8"
            try: text=raw.decode(charset,errors="replace")
            except LookupError: text=raw.decode("utf-8",errors="replace")
            return url,text,datetime.now(timezone.utc).isoformat()
        finally: connection.close()
    raise ValueError("ページの転送が多すぎます。")

class PageText(HTMLParser):
    def __init__(self): super().__init__();self.skip=0;self.text=[];self.title=[];self.in_title=False
    def handle_starttag(self,tag,attributes):
        if tag in {"script","style","noscript","svg"}: self.skip+=1
        if tag=="title": self.in_title=True
        if not self.skip and tag in {"p","br","h1","h2","h3","li","tr","section","div"}: self.text.append("\n")
    def handle_endtag(self,tag):
        if tag in {"script","style","noscript","svg"}: self.skip=max(0,self.skip-1)
        if tag=="title": self.in_title=False
    def handle_data(self,value):
        if self.in_title:self.title.append(value)
        if not self.skip:self.text.append(value)

def page(url):
    url,text,at=fetch(url);parser=PageText();parser.feed(text)
    content="\n".join(line.strip() for line in "".join(parser.text).splitlines() if line.strip())
    return {"url":url,"title":"".join(parser.title)[:300],"text":content[:20000],"clipped":len(content)>20000,"fetched_at":at}

def search(query):
    if not isinstance(query,str) or not 1<=len(query.strip())<=200: raise ValueError("検索語は1〜200文字で入力してください。")
    url="https://www.bing.com/search?format=rss&q="+urllib.parse.quote(query)
    _,text,at=fetch(url)
    root=ET.fromstring(text);results=[]
    for item in root.findall(".//item")[:8]:
        link=item.findtext("link","")
        if urllib.parse.urlsplit(link).scheme!="https": continue
        results.append({"title":html.unescape(item.findtext("title","") or "")[:300],"url":link,"snippet":html.unescape(item.findtext("description","") or "")[:1000],"fetched_at":at})
    if not results: raise ValueError("検索結果を取得できませんでした。ページURLから取得する方法も利用できます。")
    return {"provider":"Bing RSS","query":query,"fetched_at":at,"results":results}
