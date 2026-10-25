"""LaserCube UDP protocol adapter and offline demo.

Protocol reference: Wickedlasers/libLaserdockCore,
3rdparty/laserdocklib/src/LaserDockNetworkDevice.cpp.
No output commands are sent by discovery or demo mode.
"""
import collections
import ipaddress
import math
import re
import select
import socket
import struct
import threading
import time
import json
import hashlib
import copy
from pathlib import Path
from patterns import hand_frame
from svg_import import import_svg, svg_frame
from layers import compose_frame, default_layers, validate_layers
from meter import default_gauge, overlay_meter, validate_gauge
from horror import horror_frame, scene_plan
from fan import prepare_fan_asset
from show_profiles import validate_version


def decode_info(data):
    if len(data) != 64 or data[:3] != b'\x77\x00\x00':
        raise ValueError('未対応のLaserCube応答です')
    flags = data[5]
    modern = data[3] > 0 or data[4] >= 13
    return {
        'model': data[38:64].split(b'\0', 1)[0].decode('utf-8', errors='replace') or 'LaserCube',
        'firmware': '%d.%d' % (data[3], data[4]),
        'output_enabled': bool(flags & 1),
        'interlock_enabled': bool(flags & (2 if modern else 8)),
        'temperature_warning': bool(flags & (4 if modern else 16)),
        'over_temperature': bool(flags & (8 if modern else 32)),
        'temperature': struct.unpack('b', data[24:25])[0],
        'battery': data[23] if data[23] <= 100 else None,
        'dac_rate': struct.unpack_from('<I', data, 10)[0],
        'max_dac_rate': struct.unpack_from('<I', data, 14)[0],
        'buffer_free': struct.unpack_from('<H', data, 19)[0],
        'buffer_size': struct.unpack_from('<H', data, 21)[0],
        'mac': ':'.join('%02X' % byte for byte in data[26:32]),
    }


def make_frame(params, phase=0.0, blank=False, svg=None, layer_svgs=None):
    """284 moving samples bracketed by blank samples; never a static point."""
    if params['shape'] == 'layers':
        return compose_frame(params, phase, make_frame, svg, layer_svgs, blank)
    if params['shape'] == 'hands':
        return hand_frame(params, phase, blank)
    if params['shape'] == 'svg':
        if svg is None:
            raise ValueError('先にSVGを読み込んでください')
        return svg_frame(svg, params, phase, blank)
    count = 284
    angle = phase * math.tau
    cosine, sine = math.cos(angle), math.sin(angle)
    scale = params['scale'] / 100 * 2047
    colour = params['colour'].lstrip('#')
    rgb = [round(int(colour[i:i+2], 16) / 255 * 4095 * params['brightness'] / 100)
           for i in (0, 2, 4)]
    if blank:
        rgb = [0, 0, 0]
    points = []
    vertices = [(0, -1), (0.866, 0.5), (-0.866, 0.5)]
    for index in range(count):
        fraction = index / (count - 1)
        theta = fraction * math.tau
        if params['shape'] == 'eight':
            x, y = math.sin(theta), math.sin(theta * 2) * 0.65
        elif params['shape'] == 'triangle':
            edge = min(int(fraction * 3), 2)
            amount = fraction * 3 - edge
            a, b = vertices[edge], vertices[(edge + 1) % 3]
            x, y = a[0] + (b[0] - a[0]) * amount, a[1] + (b[1] - a[1]) * amount
        else:
            x, y = math.cos(theta), math.sin(theta)
        rx, ry = x * cosine - y * sine, x * sine + y * cosine
        points.append((round(2048 + rx * scale), round(2048 + ry * scale), *rgb))
    first, last = points[0], points[-1]
    return [(first[0], first[1], 0, 0, 0)] * 8 + points + [(last[0], last[1], 0, 0, 0)] * 8


def encode_samples(samples):
    """Convert screen Y (down) to scanner Y (up) once, for every shape."""
    return b''.join(struct.pack('<5H', x, 4095 - y, red, green, blue)
                    for x, y, red, green, blue in samples)


class LaserBridge:
    def __init__(self, host='192.168.1.1', command_port=45457, data_port=45458, clock=None, svg_path=None, asset_dir=None):
        address = ipaddress.ip_address(host)
        if address.version != 4 or not address.is_private:
            raise ValueError('LaserCubeにはプライベートIPv4アドレスを指定してください')
        self.host = str(address)
        self.command_port = command_port
        self.data_port = data_port
        self.clock = clock or time.monotonic
        self.lock = threading.RLock()
        self.quit = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('0.0.0.0', 0))
        self.sock.setblocking(False)
        self.mode = 'demo'
        self.running = False
        self.armed = False
        self.params = {'shape': 'circle', 'brightness': 1.0, 'scale': 12.0,
                       'speed': 0.15, 'colour': '#57e5dc', 'duration': 0.0, 'density': 4,
                       'layers': default_layers(), 'gauge': default_gauge(), 'show_version': 'v1'}
        self.show_settings = {}
        self.info = None
        self.last_seen = 0.0
        self.heartbeat = 0.0
        self.deadline = None
        self.last_error = ''
        self.sequence = 0
        self.frame_number = 0
        self.rate = 12000
        self.events = collections.deque(maxlen=20)
        self.svg = None
        self.layer_svgs = [None, None]
        self.svg_path = Path(svg_path) if svg_path else None
        self.svg_epoch = self.clock()
        self.fan_epoch = self.clock()
        self.effects = {'blood': None, 'fan': None}
        self.asset_dir = Path(asset_dir) if asset_dir else None
        for role in self.effects:
            candidates=[]
            if self.svg_path:candidates.append(self.svg_path.with_name('effect-%s.json'%role))
            if self.asset_dir:candidates.append(self.asset_dir/('effect-%s.json'%role))
            for path in candidates:
                if path.exists():
                    try:
                        asset = json.loads(path.read_text())
                        if asset.get('kind') != 'gif' or not asset.get('frames'):
                            raise ValueError('GIF輪郭データが正しくありません')
                        self.effects[role] = prepare_fan_asset(asset) if role=='fan' else asset
                        break
                    except (ValueError, OSError, TypeError) as exc:
                        self._event('GIFを読み込めません: ' + str(exc))
        if self.svg_path and self.svg_path.exists():
            try:
                self.svg = self._compile_svg(json.loads(self.svg_path.read_text()))
            except (ValueError, OSError, TypeError) as exc:
                self._event('保存したSVGを読み込めません: ' + str(exc))
        if self.svg_path:
            for index, letter in enumerate(('a', 'b')):
                path = self.svg_path.with_name('layer-%s-svg.json' % letter)
                if path.exists():
                    try:
                        self.layer_svgs[index] = self._compile_svg(json.loads(path.read_text()))
                    except (ValueError, OSError, TypeError) as exc:
                        self._event('レイヤーSVGを読み込めません: ' + str(exc))
        self._event('デモモードで起動。機器なしでも操作できます。')
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _event(self, message):
        self.events.appendleft({'time': time.strftime('%H:%M:%S'), 'message': message})

    def _command(self, packet):
        self.sock.sendto(packet, (self.host, self.command_port))

    def _send_frame(self, blank=False):
        reactive = self.params['gauge'].get('reactive', False)
        elapsed = self.clock() - self.svg_epoch if reactive or self.params['shape'] in ('svg', 'layers') else self.clock()
        if reactive:
            samples = horror_frame(self.params, elapsed*self.params['speed'],
                                   self.effects['blood'] or self.layer_svgs[1] or self.svg, blank,
                                   self.effects['fan'], (self.clock()-self.fan_epoch)*self.params['speed'])
        else:
            samples = make_frame(self.params, elapsed * self.params['speed'], blank, self.svg, self.layer_svgs)
        samples = overlay_meter(samples, self.params) if not blank else samples
        for offset in range(0, len(samples), 100):
            group = samples[offset:offset + 100]
            header = bytes((0xA9, 0, self.sequence & 255, self.frame_number & 255))
            payload = encode_samples(group)
            self.sock.sendto(header + payload, (self.host, self.data_port))
            self.sequence += 1
        self.frame_number += 1
        return len(samples)

    def _stop(self, reason, explicit=False):
        if self.mode == 'real' and (self.running or explicit):
            for _ in range(2):
                try:
                    self._command(b'\x80\x00')
                    self._command(b'\x8d')
                except OSError:
                    pass
        was_running = self.running
        self.running = False
        self.armed = False
        self.deadline = None
        if was_running or explicit:
            self._event(reason)

    def _check_real(self):
        if not self.info or self.clock() - self.last_seen > 4:
            raise RuntimeError('LaserCubeが未接続です。デモモードなら接続なしで使えます。')
        if self.info['over_temperature']:
            raise RuntimeError('LaserCubeが過熱停止を返しています。本体を冷ましてから使用してください。')
        if not self.info['interlock_enabled']:
            raise RuntimeError('本体のインターロックが有効になっていません。本体の取扱説明書を確認してください。')
        if self.info['max_dac_rate'] < 6000:
            raise RuntimeError('機器のスキャン速度を確認できません。')
        if self.command_port == 45457:
            for port in (45457, 45458):
                probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    probe.bind(('0.0.0.0', port))
                except OSError:
                    raise RuntimeError('LaserOSなどがレーザー通信ポートを使用中です。そのアプリを終了してから操作してください。')
                finally:
                    probe.close()

    def snapshot(self):
        with self.lock:
            return {'app': 'lasercube-local', 'host': self.host, 'mode': self.mode, 'running': self.running,
                    'armed': self.armed, 'connected': bool(self.info and self.clock() - self.last_seen < 4),
                    'device': self.info, 'params': dict(self.params, layers=[dict(layer) for layer in self.params['layers']], gauge=dict(self.params['gauge'])),
                    'remaining': (None if self.deadline is None else max(0, round(self.deadline - self.clock(), 1))) if self.running else 0,
                    'error': self.last_error, 'events': list(self.events),
                    'svg': self._svg_summary(self.svg),
                    'layer_svgs': [self._svg_summary(asset or self.svg) for asset in self.layer_svgs],
                    'scene': scene_plan(self.params['gauge']['value'],self.params['show_version']) if self.params['gauge'].get('reactive') else None,
                    'effects': {role:self._svg_summary(asset) for role,asset in self.effects.items()},
                    'fan_elapsed': max(0,self.clock()-self.fan_epoch),
                    'svg_elapsed': max(0, self.clock() - self.svg_epoch)}

    @staticmethod
    def _svg_summary(asset):
        return (dict({key: value for key, value in asset.items() if key not in ('frames', 'starts','frame_opacity')},
                     frame_count=len(asset['frames'])) if asset else None)

    @staticmethod
    def _compile_svg(data):
        if not isinstance(data, dict) or not isinstance(data.get('name', ''), str):
            raise ValueError('SVGファイル名が正しくありません')
        asset = import_svg(data.get('source'), data.get('name', 'drawing.svg'), data.get('ignore_pale', True))
        asset['revision'] = hashlib.sha256(json.dumps(asset, sort_keys=True).encode()).hexdigest()[:16]
        return asset

    def install_svg(self, data):
        if not isinstance(data, dict):
            raise ValueError('SVG設定が正しくありません')
        index = data.get('layer')
        if index is not None and (isinstance(index, bool) or not isinstance(index, int) or index not in (0, 1)):
            raise ValueError('レイヤーは0（A）または1（B）を指定してください')
        asset = self._compile_svg(data)
        with self.lock:
            if self.svg_path:
                self.svg_path.parent.mkdir(parents=True, exist_ok=True)
                destination = self.svg_path if index is None else self.svg_path.with_name('layer-%s-svg.json' % ('a', 'b')[index])
                temporary = destination.with_suffix('.tmp')
                temporary.write_text(json.dumps({key: data[key] for key in ('source', 'name', 'ignore_pale') if key in data}))
                temporary.replace(destination)
            if self.running:
                self._stop('SVGを読み込むため停止')
            self.armed = False
            if index is None:
                self.svg = asset
                self.params.update(shape='svg', speed=1.0 if asset['duration'] else 0.0)
            else:
                self.layer_svgs[index] = asset
                updated = [dict(layer) for layer in self.params['layers']]
                updated[index].update(shape='svg', enabled=True, speed=1.0 if asset['duration'] else 0.0)
                self.params.update(shape='layers', speed=1.0, layers=updated)
            self.svg_epoch = self.clock()
            self._event('SVG読込: %s（%dコマ）' % (asset['name'], len(asset['frames'])))
            return self.snapshot()

    def svg_asset(self, layer=None):
        with self.lock:
            return (self.layer_svgs[layer] or self.svg) if layer is not None else self.svg

    def effect_assets(self):
        with self.lock:
            return self.effects

    def action(self, path, data):
        with self.lock:
            if path == '/api/heartbeat':
                self.heartbeat = self.clock()
            elif path == '/api/version':
                version=validate_version(data.get('version'))
                if version!=self.params['show_version']:
                    current=self.params['show_version']
                    self.show_settings[current]=copy.deepcopy(self.params)
                    updated=copy.deepcopy(self.show_settings.get(version,self.params))
                    updated['show_version']=version
                    self._stop('演出バージョンを切り替えるため停止')
                    self.params=updated
                    self.svg_epoch=self.fan_epoch=self.clock()
                    self._event(version+'の演出へ切り替えました')
            elif path == '/api/params':
                updated = dict(self.params)
                if 'show_version' in data:
                    version=validate_version(data['show_version'])
                    if version!=self.params['show_version']:
                        raise ValueError('v1 / v2の切り替えボタンを使ってください')
                for key, limits in {'brightness': (0, 5), 'scale': (5, 30), 'speed': (0, 1)}.items():
                    if key in data:
                        value = float(data[key])
                        if not math.isfinite(value) or not limits[0] <= value <= limits[1]:
                            raise ValueError(key + ' が範囲外です')
                        updated[key] = value
                if 'duration' in data:
                    value = float(data['duration'])
                    if isinstance(data['duration'], bool) or not math.isfinite(value) or not (value == 0 or 5 <= value <= 30):
                        raise ValueError('実行時間は0（停止するまで）または5〜30秒を指定してください')
                    updated['duration'] = value
                if 'density' in data:
                    value = data['density']
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value or not 1 <= value <= 8:
                        raise ValueError('わらわら度は1〜8の整数を指定してください')
                    updated['density'] = int(value)
                if 'shape' in data:
                    if data['shape'] not in ('circle', 'eight', 'triangle', 'hands', 'svg', 'layers'):
                        raise ValueError('未対応の図形です')
                    if data['shape'] == 'svg' and not self.svg:
                        raise ValueError('先にSVGを読み込んでください')
                    updated['shape'] = data['shape']
                    if data['shape'] == 'hands' and 'colour' not in data:
                        updated['colour'] = '#ed1c24'
                if 'colour' in data:
                    if not isinstance(data['colour'], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', data['colour']):
                        raise ValueError('色が正しくありません')
                    updated['colour'] = data['colour']
                if 'layers' in data:
                    updated['layers'] = validate_layers(data['layers'], self.params['layers'])
                if 'gauge' in data:
                    updated['gauge'] = validate_gauge(data['gauge'], self.params['gauge'])
                was_empty = self.params['gauge'].get('reactive') and self.params['gauge']['value'] <= 0
                now_empty = updated['gauge'].get('reactive') and updated['gauge']['value'] <= 0
                if now_empty and not was_empty:
                    self.fan_epoch = self.clock()
                if 'duration' in data and self.running and updated['duration'] != self.params['duration']:
                    self.deadline = None if updated['duration'] == 0 else self.clock() + updated['duration']
                self.params = updated
            elif path == '/api/mode':
                if data.get('mode') not in ('demo', 'real'):
                    raise ValueError('モードが正しくありません')
                self._stop('モード変更で停止')
                self.mode = data['mode']
                self._event('デモモード' if self.mode == 'demo' else '実機モード：出力はまだ許可されていません')
            elif path == '/api/arm':
                if data.get('acknowledge') is not True:
                    raise ValueError('投影先と緊急停止手段を確認してください')
                if self.mode == 'real':
                    self._check_real()
                self.armed = True
                self.heartbeat = self.clock()
                self._event('出力操作を許可しました')
            elif path == '/api/start':
                if self.quit.is_set() or not self.thread.is_alive():
                    raise RuntimeError('通信処理が停止しています。アプリを再起動してください')
                if self.running:
                    raise RuntimeError('すでに実行中です。停止してから開始してください。')
                self.svg_epoch = self.clock()
                self.fan_epoch = self.svg_epoch
                if self.mode == 'real':
                    self._check_real()
                    if not self.armed or self.clock() - self.heartbeat > 3:
                        raise RuntimeError('画面で出力を許可してから開始してください')
                    self.rate = min(12000, self.info['max_dac_rate'])
                    try:
                        self._command(b'\x80\x00')
                        self._command(b'\x8d')
                        self._command(b'\x82' + struct.pack('<I', self.rate))
                        self._send_frame(blank=True)
                        self._command(b'\x80\x01')
                        self._command(b'\x80\x01')
                    except OSError:
                        self._stop('出力開始に失敗したため停止', explicit=True)
                        raise
                self.running = True
                self.heartbeat = self.clock()
                self.deadline = None if self.params['duration'] == 0 else self.clock() + self.params['duration']
                self._event('デモを開始' if self.mode == 'demo' else '実機出力を開始')
            elif path == '/api/stop':
                self._stop('停止ボタンで停止', explicit=True)
            else:
                raise ValueError('未対応の操作です')
            return self.snapshot()

    def _loop(self):
        next_probe = next_frame = self.clock()
        while not self.quit.is_set():
            try:
                with self.lock:
                    now = self.clock()
                    if now >= next_probe:
                        try:
                            self._command(b'\x77')
                        except OSError:
                            pass
                        next_probe = now + 1
                    for _ in range(20):
                        ready, _, _ = select.select([self.sock], [], [], 0)
                        if not ready:
                            break
                        packet, peer = self.sock.recvfrom(2048)
                        if peer[0] == self.host and len(packet) == 64 and packet[:3] == b'\x77\x00\x00':
                            first_connection = not self.info or now - self.last_seen > 4
                            self.info = decode_info(packet)
                            self.last_seen = now
                            if first_connection:
                                self._event('接続：' + self.info['model'])
                    if self.running:
                        if self.deadline is not None and now >= self.deadline:
                            self._stop('指定時間で自動停止')
                        elif now - self.heartbeat > 3:
                            self._stop('操作画面との通信が途切れたため停止')
                        elif self.mode == 'real':
                            try:
                                self._check_real()
                                if now >= next_frame:
                                    sample_count = self._send_frame()
                                    next_frame = now + sample_count / self.rate
                            except (OSError, RuntimeError) as exc:
                                self.last_error = str(exc)
                                self._stop(str(exc))
            except OSError as exc:
                if not self.quit.is_set():
                    with self.lock:
                        self.last_error = str(exc)
                        self._stop('通信エラーで停止')
            except Exception as exc:
                with self.lock:
                    self.last_error = '内部エラー: ' + str(exc)
                    self._stop('通信処理の内部エラーで停止')
                self.quit.set()
            self.quit.wait(0.003)

    def close(self):
        self.quit.set()
        self.thread.join(timeout=2)
        with self.lock:
            self._stop('アプリ終了で停止')
        self.sock.close()
