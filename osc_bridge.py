"""Optional OSC input and APC40-to-OSC output, local Mac only."""
import collections
import ipaddress
import math
import socket
import threading
import time

from osc_protocol import decode_packet, encode_message


class OSCBridge:
    def __init__(self, laser):
        self.laser = laser
        self.lock = threading.RLock()
        self.quit = threading.Event()
        self.receiver = None
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.config = {'rx_enabled': False, 'rx_port': 9000, 'tx_enabled': False,
                       'tx_host': '127.0.0.1', 'tx_port': 9001}
        self.received = self.sent = 0
        self.last_rx = self.last_tx = self.error = ''
        self.times = collections.deque(maxlen=500)
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return dict(self.config, rx_host='127.0.0.1', received=self.received,
                        sent=self.sent, last_rx=self.last_rx, last_tx=self.last_tx,
                        error=self.error)

    def configure(self, data):
        with self.lock:
            updated = dict(self.config)
            for key in ('rx_enabled', 'tx_enabled'):
                if key in data:
                    if not isinstance(data[key], bool):
                        raise ValueError('OSCのON/OFFは真偽値で指定してください')
                    updated[key] = data[key]
            for key in ('rx_port', 'tx_port'):
                if key in data:
                    value = data[key]
                    if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 65535:
                        raise ValueError('OSCポートは1024〜65535を指定してください')
                    updated[key] = value
            if 'tx_host' in data:
                value = '127.0.0.1' if data['tx_host'] == 'localhost' else data['tx_host']
                try:
                    address = ipaddress.ip_address(value)
                except (ValueError, TypeError):
                    raise ValueError('OSC送信先は127.0.0.1またはlocalhostを指定してください')
                if address.version != 4 or not address.is_loopback:
                    raise ValueError('この初版のOSC送受信は同じMac内に限定しています')
                updated['tx_host'] = str(address)
            if updated['tx_enabled'] and updated['rx_enabled'] and updated['tx_port'] == updated['rx_port']:
                raise ValueError('OSCの送信先と受信ポートは別にしてください')
            changed_rx = updated['rx_enabled'] != self.config['rx_enabled'] or updated['rx_port'] != self.config['rx_port']
            replacement = None
            if changed_rx and updated['rx_enabled']:
                replacement = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    replacement.bind(('127.0.0.1', updated['rx_port']))
                    replacement.settimeout(.1)
                except OSError:
                    replacement.close()
                    raise RuntimeError('OSC受信ポートを使用できません。別のポート番号を指定してください')
            old = self.receiver
            if changed_rx:
                self.receiver = replacement
                if old:
                    old.close()
            self.config = updated
            self.error = ''
        return self.snapshot()

    def _route(self, address, args):
        if address in ('/laser/start', '/laser/stop', '/laser/next'):
            if args and not args[0]:
                return
            if address == '/laser/next':
                shapes = ['circle', 'eight', 'triangle', 'hands', 'layers'] + (['svg'] if self.laser.snapshot().get('svg') else [])
                current = self.laser.snapshot()['params']['shape']
                self.laser.action('/api/params', {'shape': shapes[(shapes.index(current) + 1) % len(shapes)]})
            else:
                self.laser.action('/api/start' if address.endswith('/start') else '/api/stop', {})
            return
        if len(args) != 1:
            raise ValueError('このOSCアドレスには引数を1つ送ってください')
        if address in ('/laser/brightness', '/laser/size', '/laser/speed', '/laser/density'):
            value = args[0]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('明るさ・大きさ・速度のOSC値は0〜1です')
            key, output = {'/laser/brightness': ('brightness', value * 5),
                           '/laser/size': ('scale', 5 + value * 25),
                           '/laser/speed': ('speed', value),
                           '/laser/density': ('density', 1 + int(value * 7 + .5))}[address]
        elif address == '/laser/shape':
            key, output = 'shape', args[0]
        elif address == '/laser/colour':
            key, output = 'colour', args[0]
        elif address == '/laser/duration':
            key, output = 'duration', args[0]
        else:
            raise ValueError('未対応のOSCアドレス: ' + address)
        self.laser.action('/api/params', {key: output})

    def _listen(self):
        while not self.quit.is_set():
            with self.lock:
                receiver = self.receiver
            if not receiver:
                self.quit.wait(.05)
                continue
            try:
                packet, _ = receiver.recvfrom(16385)
            except socket.timeout:
                continue
            except OSError:
                continue
            try:
                with self.lock:
                    if self.receiver is not receiver or not self.config['rx_enabled']:
                        continue
                messages = decode_packet(packet)
                now = time.monotonic()
                with self.lock:
                    while self.times and now - self.times[0] > 1:
                        self.times.popleft()
                    if len(self.times) + len(messages) > 400:
                        raise ValueError('OSC入力が多すぎます。送信頻度を下げてください')
                    self.times.extend([now] * len(messages))
                with self.laser.lock:
                    for address, args in messages:
                        try:
                            self._route(address, args)
                            with self.lock:
                                self.received += 1
                                self.last_rx = address + ' ' + str(args)
                                self.error = ''
                        except (ValueError, RuntimeError, OSError) as exc:
                            with self.lock:
                                self.error = str(exc)
            except (ValueError, RuntimeError) as exc:
                with self.lock:
                    self.error = str(exc)

    def forward_midi(self, data):
        values = [data.get(key) for key in ('status', 'index', 'value')]
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in values):
            raise ValueError('MIDI値は整数で指定してください')
        status, index, value = values
        if not 0 <= status <= 255 or not 0 <= index <= 127 or not 0 <= value <= 127 or status & 0xf0 not in (0x80, 0x90, 0xb0):
            raise ValueError('未対応のMIDIメッセージです')
        with self.lock:
            if not self.config['tx_enabled']:
                return
            kind = 'cc' if status & 0xf0 == 0xb0 else 'note'
            address = '/apc40/%s/%d/%d' % (kind, (status & 15) + 1, index)
            normal = 0.0 if status & 0xf0 == 0x80 else value / 127
            self.sender.sendto(encode_message(address, [normal]), (self.config['tx_host'], self.config['tx_port']))
            self.sent += 1
            self.last_tx = address + ' ' + ('%.3f' % normal)

    def close(self):
        self.quit.set()
        with self.lock:
            if self.receiver:
                self.receiver.close()
                self.receiver = None
        self.thread.join(timeout=1)
        self.sender.close()
