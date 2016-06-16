#!/usr/bin/env python
#
# Simple asynchronous HTTP proxy with tunnelling (CONNECT).
#
# GET/POST proxying based on
# http://groups.google.com/group/python-tornado/msg/7bea08e7a049cf26
#
# Copyright (C) 2012 Senko Rasic <senko.rasic@dobarkod.hr>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import logging
import os
import requests
import sys
import socket
from urlparse import urlparse
import re
import tornado.httpserver
import tornado.ioloop
import tornado.iostream
import tornado.web
import tornado.httpclient
import tornado.httputil
import json
import urllib
import xml.dom.minidom
logger = logging.getLogger('tornado_proxy')

__all__ = ['ProxyHandler', 'run_proxy']


'''
location /eapi/song/enhance/player/url
{
    proxy_pass
http: // localhost:5001;
}

location /eapi/song/enhance/download/url
{
    proxy_pass
http: // localhost:5001;
}
'''
HEADERS = {
    'User-Agent':
    'Mozilla/5.0 (compatible; MSIE 9.0; Windows NT 7.1; Trident/5.0)',

    'Referer': 'http://www.xiami.com/song/play'
}


def get_real_url(url):
    r = requests.get(url, allow_redirects=False)
    return r.headers["location"]

def get_music_info(id):
    strid=str(id)
    r=requests.get("http://music.163.com/api/song/detail?id=" +strid + "&ids=[" + strid + "]")
    json_r=json.loads(r.content)
    m_name=json_r["songs"][0]["name"]
    m_artist=json_r["songs"][0]["artists"][0]["name"]
    print m_name,m_artist
    return m_name,m_artist

def decode_location(location):
    if not location:
        return None

    url = location[1:]
    urllen = len(url)
    rows = int(location[0:1])

    cols_base = urllen / rows  # basic column count
    rows_ex = urllen % rows    # count of rows that have 1 more column

    matrix = []
    for r in xrange(rows):
        length = cols_base + 1 if r < rows_ex else cols_base
        matrix.append(url[:length])
        url = url[length:]

    url = ''
    for i in xrange(urllen):
        url += matrix[i % rows][i / rows]

    return urllib.unquote(url).replace('^', '0')


def get_xiami_music_url(url):
    id=url.replace("http://www.xiami.com/song/","")
    r = requests.get("http://www.xiami.com/song/playlist/id/"+ id +"/object_name/default/object_id/0/cat/json",headers=HEADERS)
    jmusic=json.loads(r.content)
    enurl=jmusic["data"]["trackList"][0]["location"]
    print  "found music url:" + decode_location(enurl)
    return decode_location(enurl)

def get_kuwo_music_url(url):
    id=url.replace("http://bd.kuwo.cn/yinyue/","")
    id = id.replace("?from=baidu", "")
    r = requests.get("http://player.kuwo.cn/webmusic/st/getNewMuiseByRid?rid=MUSIC_" + id)
    doc = xml.dom.minidom.parseString(r.content.replace("&","&amp;"))
    node1=doc.getElementsByTagName("mp3path")[0]
    mp3path= node1.childNodes[0].data
    node2=doc.getElementsByTagName("mp3dl")[0]
    mp3server= node2.childNodes[0].data
    murl="http://" +mp3server+ "/resource/" +mp3path
    print  "found music url:" + murl
    return murl

def get_qq_music_url(url):
    id=url.replace("http://y.qq.com/#type=song&play=1&mid=","")
    id = id.replace("&ADTAG=baiduald", "")
    r = requests.get("http://i.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg?songmid=" + id +"&tpl=yqq_song_detail&play=1&ADTAG=baiduald")
    r1 = re.compile(r'songid":(.*?),')
    m = re.findall(r1, r.content)
    sid=m[0]
    murl="http://stream.qqmusic.tc.qq.com/" + sid +".mp3"
    print  "found music url:" + murl
    return murl

def get_baidu_music_url(url):
    r = requests.get(url)
    r1 = re.compile(r'data-musicid="(.*?)"')
    m = re.findall(r1, r.content)
    r2= requests.get("http://music.baidu.com/data/music/fmlink?songIds=" + m[0] +"&type=mp3&rate=320")
    jmusic=json.loads(r2.content)
    print  "found music url:" + jmusic["data"]["songList"][0]["songLink"]
    return jmusic["data"]["songList"][0]["songLink"]

def find_url_mode_1(content):
    m=[]
    idx=1
    while content.find("data-id",idx)>0:
        r = re.compile(r'href="(.*?)"')
        m2=re.findall(r,content[idx::])
        if len(m2)>0:
            m.append(m2[0])
        idx = content.find("data-id", idx) + 20
    return m


def get_replaced_music_url(m_name,m_artist):
    r = requests.get("http://www.baidu.com/s?wd=" + m_name + "%20" + m_artist)
    #search for single song
    r1 = re.compile(r'data-id="(.*?)"')
    #m = re.findall(r1, r.content)
    m=find_url_mode_1(r.content)
    print "try mode 1"

    if len(m)<2:
        print "switch to mode 2."
        r1 = re.compile(r'music-data="(.*?)"')
        m = re.findall(r1, r.content)

    r1 = re.compile(r"http://www.baidu.com/link(.*?)'")
    m2 = re.findall(r1, str(m))

    realm = []
    #print m2
    for item in m2:
        real_url=get_real_url("http://www.baidu.com/link" + item)
        item=real_url
        realm.append(real_url)
        print "candidate url: " + real_url

    # you can adjust priority of the sources by changing the code order
    for item in realm:
        if item.find("http://y.qq.com/")!=-1:
            print "found QQMusic,Analyzing music url..."
            return get_qq_music_url(item)

    for item in realm:
        if item.find("http://www.xiami.com/song/")!=-1:
            print "found XiamiMusic,Analyzing music url..."
            return get_xiami_music_url(item)


    for item in realm:
        if item.find("http://bd.kuwo.cn/yinyue/")!=-1:
            print "found KuwoMusic,Analyzing music url..."
            return get_kuwo_music_url(item)

    for item in m2:
        if item.find("http://www.xiami.com/song/")!=-1:
            print "found XiamiMusic,Analyzing music url..."
            return get_xiami_music_url(item)


def deal(url,body):
    if url=="http://music.163.com/eapi/v3/song/detail/" or url=="http://music.163.com/eapi/batch":
        #print body[0:1000]
        body=re.sub('"st":-.+?,', '"st":0,',body)
        body=re.sub('"pl":0' ,'"pl":320000', body)
        body = re.sub('"dl":0' ,'"dl":320000', body)
        body = re.sub('"sp":0' ,'"sp":7', body)
        body = re.sub('"cp":0', '"cp":1', body)
        body = re.sub('"subp":0' ,'"subp":1', body)
        body = re.sub('"fl":0', '"fl":320000', body)
        body = re.sub('"fee":.+?,', '"fee":0,', body)
        body = re.sub( '"abroad":1,' ,'', body)

    if url=="http://music.163.com/eapi/song/enhance/player/url":
        jplayinfo=json.loads(body)
        if jplayinfo["data"][0]["code"]!=200:
            print "player music id: " + str(jplayinfo["data"][0]["id"]) + " not found,trying to redirect..."
            m_name,m_artist=get_music_info(jplayinfo["data"][0]["id"])
            newurl = get_replaced_music_url(m_name, m_artist)
            jplayinfo["data"][0]["url"]=newurl
            jplayinfo["data"][0]["code"]=200
            jplayinfo["data"][0]["br"] = 320000
            jplayinfo["data"][0]["md5"] = 0
            jplayinfo["data"][0]["size"] = 0
        body=json.dumps(jplayinfo)

    if url == "http://music.163.com/eapi/song/enhance/download/url":
            print body
            jplayinfo = json.loads(body)
            if jplayinfo["data"]["code"] != 200:
                print "download music id: " + str(jplayinfo["data"]["id"]) + " not found,trying to redirect..."
                m_name, m_artist = get_music_info(jplayinfo["data"]["id"])
                newurl = get_replaced_music_url(m_name, m_artist)
                jplayinfo["data"]["url"] = newurl
                jplayinfo["data"]["code"] = 200
                jplayinfo["data"]["br"] = 320000
                jplayinfo["data"]["md5"] = 0
                jplayinfo["data"]["size"] = 0
            body = json.dumps(jplayinfo)

    #print body

    return body

def get_proxy(url):
    url_parsed = urlparse(url, scheme='http')
    proxy_key = '%s_proxy' % url_parsed.scheme
    return os.environ.get(proxy_key)


def parse_proxy(proxy):
    proxy_parsed = urlparse(proxy, scheme='http')
    return proxy_parsed.hostname, proxy_parsed.port


def fetch_request(url, callback, **kwargs):
    proxy = get_proxy(url)
    if proxy:
        logger.debug('Forward request via upstream proxy %s', proxy)
        tornado.httpclient.AsyncHTTPClient.configure(
            'tornado.curl_httpclient.CurlAsyncHTTPClient')
        host, port = parse_proxy(proxy)
        kwargs['proxy_host'] = host
        kwargs['proxy_port'] = port

    req = tornado.httpclient.HTTPRequest(url, **kwargs)
    client = tornado.httpclient.AsyncHTTPClient()
    client.fetch(req, callback, raise_error=False)


class ProxyHandler(tornado.web.RequestHandler):
    SUPPORTED_METHODS = ['GET', 'POST', 'CONNECT']
    
    def compute_etag(self):
        return None # disable tornado Etag

    @tornado.web.asynchronous
    def get(self):
        logger.debug('Handle %s request to %s', self.request.method,
                     self.request.uri)

        def handle_response(response):
            if (response.error and not
                    isinstance(response.error, tornado.httpclient.HTTPError)):
                self.set_status(500)
                self.write('Internal server error:\n' + str(response.error))
            else:
                self.set_status(response.code, response.reason)
                self._headers = tornado.httputil.HTTPHeaders() # clear tornado default header
                
                for header, v in response.headers.get_all():
                    if header not in ('Content-Length', 'Transfer-Encoding', 'Content-Encoding', 'Connection'):
                        self.add_header(header, v) # some header appear multiple times, eg 'Set-Cookie'
                
                if response.body:
                    resp=""
                    if response.effective_url.find("http://music.163.com/eapi")!=-1:
                        print "return url:" + response.effective_url
                        #print response.body[1:1000]
                        #print "length:" + str(len(response.body))
                        resp = deal(response.effective_url,response.body)
                    else:
                        print "return raw url:" + response.effective_url
                        resp=response.body

                    self.set_header('Content-Length', len(resp))
                    self.write(resp)
            self.finish()

        body = self.request.body
        #print "body:" + body[0:1000]
        #print "body-length:" + str(len(body))
        if not body:
            body = None
        try:
            if 'Proxy-Connection' in self.request.headers:
                del self.request.headers['Proxy-Connection']
            fetch_request(
                self.request.uri, handle_response,
                method=self.request.method, body=body,
                headers=self.request.headers, follow_redirects=False,
                allow_nonstandard_methods=True)
        except tornado.httpclient.HTTPError as e:
            if hasattr(e, 'response') and e.response:
                handle_response(e.response)
            else:
                self.set_status(500)
                self.write('Internal server error:\n' + str(e))
                self.finish()

    @tornado.web.asynchronous
    def post(self):
        return self.get()

    @tornado.web.asynchronous
    def connect(self):
        logger.debug('Start CONNECT to %s', self.request.uri)
        host, port = self.request.uri.split(':')
        client = self.request.connection.stream

        def read_from_client(data):
            upstream.write(data)

        def read_from_upstream(data):
            client.write(data)

        def client_close(data=None):
            if upstream.closed():
                return
            if data:
                upstream.write(data)
            upstream.close()

        def upstream_close(data=None):
            if client.closed():
                return
            if data:
                client.write(data)
            client.close()

        def start_tunnel():
            logger.debug('CONNECT tunnel established to %s', self.request.uri)
            client.read_until_close(client_close, read_from_client)
            upstream.read_until_close(upstream_close, read_from_upstream)
            client.write(b'HTTP/1.0 200 Connection established\r\n\r\n')

        def on_proxy_response(data=None):
            if data:
                first_line = data.splitlines()[0]
                http_v, status, text = first_line.split(None, 2)
                if int(status) == 200:
                    logger.debug('Connected to upstream proxy %s', proxy)
                    start_tunnel()
                    return

            self.set_status(500)
            self.finish()

        def start_proxy_tunnel():
            upstream.write('CONNECT %s HTTP/1.1\r\n' % self.request.uri)
            upstream.write('Host: %s\r\n' % self.request.uri)
            upstream.write('Proxy-Connection: Keep-Alive\r\n\r\n')
            upstream.read_until('\r\n\r\n', on_proxy_response)

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
        upstream = tornado.iostream.IOStream(s)

        proxy = get_proxy(self.request.uri)
        if proxy:
            proxy_host, proxy_port = parse_proxy(proxy)
            upstream.connect((proxy_host, proxy_port), start_proxy_tunnel)
        else:
            upstream.connect((host, int(port)), start_tunnel)


def run_proxy(port, start_ioloop=True):
    """
    Run proxy on the specified port. If start_ioloop is True (default),
    the tornado IOLoop will be started immediately.
    """
    app = tornado.web.Application([
        (r'.*', ProxyHandler),
    ])
    app.listen(port)
    ioloop = tornado.ioloop.IOLoop.instance()
    if start_ioloop:
        ioloop.start()

if __name__ == '__main__':
    port = 8888
    if len(sys.argv) > 1:
        port = int(sys.argv[1])

    print ("Starting HTTP proxy on port %d" % port)
    run_proxy(port)

