"""Small OSC/UDP codec: int32, float32, strings, booleans, immediate bundles.

Reference: https://opensoundcontrol.stanford.edu/spec-1_0.html
Future-timed bundles are rejected rather than executed early.
"""
import math
import struct
import time


def _string(value):
    if '\0' in value:
        raise ValueError('OSC文字列にNULは使用できません')
    raw = value.encode('utf-8') + b'\0'
    return raw + b'\0' * (-len(raw) % 4)


def encode_message(address, args=()):
    if not isinstance(address, str) or not address.startswith('/'):
        raise ValueError('OSCアドレスは / で始めてください')
    tags, payload = ',', bytearray()
    for value in args:
        if isinstance(value, bool):
            tags += 'T' if value else 'F'
        elif isinstance(value, int):
            if not -(2**31) <= value < 2**31:
                raise ValueError('OSC整数がint32の範囲外です')
            tags += 'i'
            payload.extend(struct.pack('>i', value))
        elif isinstance(value, float):
            if not math.isfinite(value) or abs(value) > 3.4028234e38:
                raise ValueError('OSC浮動小数点数が範囲外です')
            tags += 'f'
            payload.extend(struct.pack('>f', value))
        elif isinstance(value, str):
            tags += 's'
            payload.extend(_string(value))
        else:
            raise ValueError('未対応のOSC引数です')
    return _string(address) + _string(tags) + payload


def _read_string(data, offset):
    end = data.find(b'\0', offset)
    if end < 0:
        raise ValueError('OSC文字列が終端されていません')
    following = (end + 4) & ~3
    if following > len(data) or any(data[end:following]):
        raise ValueError('OSC文字列のパディングが不正です')
    try:
        return data[offset:end].decode('utf-8'), following
    except UnicodeDecodeError:
        raise ValueError('OSC文字列を読み取れません')


def decode_packet(data, depth=0):
    if not data or len(data) % 4 or len(data) > 16384 or depth > 8:
        raise ValueError('OSCパケットのサイズまたは入れ子が不正です')
    if data.startswith(b'#bundle\0'):
        if len(data) < 16:
            raise ValueError('OSCバンドルが短すぎます')
        timestamp = struct.unpack_from('>Q', data, 8)[0]
        if timestamp > 1:
            scheduled = (timestamp >> 32) - 2208988800 + (timestamp & 0xffffffff) / 2**32
            if scheduled > time.time() + .001:
                raise ValueError('未来時刻のOSCバンドルには未対応です。即時送信を使ってください')
        result, offset = [], 16
        while offset < len(data):
            if offset + 4 > len(data):
                raise ValueError('OSCバンドルの要素サイズが不正です')
            size = struct.unpack_from('>i', data, offset)[0]
            offset += 4
            if size <= 0 or offset + size > len(data):
                raise ValueError('OSCバンドルの要素サイズが不正です')
            result.extend(decode_packet(data[offset:offset + size], depth + 1))
            offset += size
            if len(result) > 256:
                raise ValueError('OSCバンドル内のメッセージが多すぎます')
        return result
    address, offset = _read_string(data, 0)
    tags, offset = _read_string(data, offset)
    if not address.startswith('/') or not tags.startswith(','):
        raise ValueError('OSCアドレスまたは型タグが不正です')
    args = []
    for tag in tags[1:]:
        if tag in 'if':
            if offset + 4 > len(data):
                raise ValueError('OSC引数が不足しています')
            value = struct.unpack_from('>i' if tag == 'i' else '>f', data, offset)[0]
            if tag == 'f' and not math.isfinite(value):
                raise ValueError('OSC引数が有限数ではありません')
            offset += 4
        elif tag == 's':
            value, offset = _read_string(data, offset)
        elif tag in 'TF':
            value = tag == 'T'
        else:
            raise ValueError('未対応のOSC型タグ: ' + tag)
        args.append(value)
    if offset != len(data):
        raise ValueError('OSCパケットに余分なデータがあります')
    return [(address, args)]
