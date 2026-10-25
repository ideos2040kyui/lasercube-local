#!/usr/bin/env python3
"""Loopback-only LaserCube controller. Python standard library only."""
import argparse
import http.client
import json
import secrets
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from laser import LaserBridge
from osc_bridge import OSCBridge
from show_profiles import SHOWS, catalog, preset_for

ROOT = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(32)
BRIDGE = None
OSC = None


class Handler(BaseHTTPRequestHandler):
    def reply(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(payload)

    def valid_host(self):
        return self.headers.get('Host', '').split(':')[0] in ('localhost', '127.0.0.1')

    def do_GET(self):
        if not self.valid_host():
            return self.reply({'error': 'Invalid host'}, 403)
        path = self.path.split('?', 1)[0]
        if path == '/api/state':
            return self.reply(dict(BRIDGE.snapshot(), osc=OSC.snapshot(), token=TOKEN))
        if path == '/api/shows':
            return self.reply(catalog())
        if path == '/api/svg':
            value = parse_qs(self.path.partition('?')[2]).get('layer')
            if value is not None and value not in (['0'], ['1']):
                return self.reply({'error': 'Invalid layer'}, 400)
            return self.reply(BRIDGE.svg_asset(int(value[0]) if value else None))
        if path == '/api/effects':
            return self.reply(BRIDGE.effect_assets())
        files = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/app.js': ('app.js', 'application/javascript; charset=utf-8'),
                 '/style.css': ('style.css', 'text/css; charset=utf-8'),
                 '/osc.css': ('osc.css', 'text/css; charset=utf-8'),
                 '/horror.css': ('horror.css', 'text/css; charset=utf-8'),
                 '/hand.json': ('hand.json', 'application/json; charset=utf-8')}
        files['/gauge.json'] = ('gauge.json', 'application/json; charset=utf-8')
        files['/horror-scene.json'] = ('horror-scene.json', 'application/json; charset=utf-8')
        if path not in files:
            return self.reply({'error': 'Not found'}, 404)
        name, mime = files[path]
        data = (ROOT / 'static' / name).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self.valid_host() or not secrets.compare_digest(self.headers.get('X-Local-Token', ''), TOKEN):
            return self.reply({'error': 'ローカル画面から操作してください'}, 403)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            limit = 3 * 1024 * 1024 if self.path == '/api/svg' else 8192
            if size < 0 or size > limit:
                raise ValueError('Request too large')
            body = json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('Invalid request')
            if self.path == '/api/svg':
                result = BRIDGE.install_svg(body)
            elif self.path == '/api/osc/config':
                OSC.configure(body)
                result = BRIDGE.snapshot()
            elif self.path == '/api/osc/midi':
                OSC.forward_midi(body)
                result = BRIDGE.snapshot()
            else:
                result = BRIDGE.action(self.path, body)
            self.reply(dict(result, osc=OSC.snapshot()))
        except (ValueError, RuntimeError, OSError) as exc:
            self.reply({'error': str(exc)}, 400)

    def log_message(self, fmt, *args):
        if args and 'heartbeat' not in str(args[0]) and '/api/state' not in str(args[0]):
            super().log_message(fmt, *args)


def main():
    global BRIDGE, OSC
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--device', default='192.168.1.1')
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--show', choices=tuple(SHOWS))
    args = parser.parse_args()
    url = 'http://localhost:%d' % args.port

    def open_page():
        for name in ('Google Chrome', 'Microsoft Edge'):
            if (Path('/Applications') / (name + '.app')).exists():
                subprocess.Popen(['/usr/bin/open', '-a', name, url])
                return
        webbrowser.open(url)

    existing = http.client.HTTPConnection('127.0.0.1', args.port, timeout=0.4)
    try:
        existing.request('GET', '/api/state')
        response = existing.getresponse()
        data = json.loads(response.read())
        if data.get('app') == 'lasercube-local':
            if args.show and args.show!=data.get('params',{}).get('show_version'):
                existing.request('POST','/api/version',json.dumps({'version':args.show}),
                                 {'Content-Type':'application/json','X-Local-Token':data.get('token','')})
                switched=existing.getresponse()
                payload=json.loads(switched.read())
                if switched.status!=200:
                    print(payload.get('error','起動中のアプリを再起動してください'),flush=True)
                    return
            print('すでに起動しています: ' + url, flush=True)
            if args.open:
                open_page()
            return
    except (OSError, ValueError, http.client.HTTPException):
        pass
    finally:
        existing.close()
    BRIDGE = LaserBridge(args.device, svg_path=ROOT / '.runtime' / 'imported-svg.json', asset_dir=ROOT / 'assets')
    BRIDGE.action('/api/params', preset_for('v1'))
    if args.show and args.show!='v1':
        BRIDGE.action('/api/version',{'version':args.show})
        BRIDGE.action('/api/params',preset_for(args.show))
    OSC = OSCBridge(BRIDGE)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print('LaserCube Local: ' + url, flush=True)
    print('デモモードで起動しました。終了: Control+C', flush=True)
    if args.open:
        threading.Timer(0.7, open_page).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        OSC.close()
        BRIDGE.close()
        server.server_close()


if __name__ == '__main__':
    main()
