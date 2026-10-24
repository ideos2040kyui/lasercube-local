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
from patterns import hand_frame


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


def make_frame(params, phase=0.0, blank=False):
    """284 moving samples bracketed by blank samples; never a static point."""
    if params['shape'] == 'hands':
        return hand_frame(params, phase, blank)
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


class LaserBridge:
    def __init__(self, host='192.168.1.1', command_port=45457, data_port=45458, clock=None):
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
                       'speed': 0.15, 'colour': '#57e5dc', 'duration': 0.0, 'density': 4}
        self.info = None
        self.last_seen = 0.0
        self.heartbeat = 0.0
        self.deadline = None
        self.last_error = ''
        self.sequence = 0
        self.frame_number = 0
        self.rate = 12000
        self.events = collections.deque(maxlen=20)
        self._event('デモモードで起動。機器なしでも操作できます。')
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _event(self, message):
        self.events.appendleft({'time': time.strftime('%H:%M:%S'), 'message': message})

    def _command(self, packet):
        self.sock.sendto(packet, (self.host, self.command_port))

    def _send_frame(self, blank=False):
        samples = make_frame(self.params, self.clock() * self.params['speed'], blank)
        for offset in range(0, len(samples), 100):
            group = samples[offset:offset + 100]
            header = bytes((0xA9, 0, self.sequence & 255, self.frame_number & 255))
            payload = b''.join(struct.pack('<5H', *point) for point in group)
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
                    'device': self.info, 'params': dict(self.params),
                    'remaining': (None if self.deadline is None else max(0, round(self.deadline - self.clock(), 1))) if self.running else 0,
                    'error': self.last_error, 'events': list(self.events)}

    def action(self, path, data):
        with self.lock:
            if path == '/api/heartbeat':
                self.heartbeat = self.clock()
            elif path == '/api/params':
                updated = dict(self.params)
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
                    if data['shape'] not in ('circle', 'eight', 'triangle', 'hands'):
                        raise ValueError('未対応の図形です')
                    updated['shape'] = data['shape']
                    if data['shape'] == 'hands' and 'colour' not in data:
                        updated['colour'] = '#ed1c24'
                if 'colour' in data:
                    if not isinstance(data['colour'], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', data['colour']):
                        raise ValueError('色が正しくありません')
                    updated['colour'] = data['colour']
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
