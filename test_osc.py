import struct
import time
import unittest
import socket

from laser import LaserBridge
from osc_bridge import OSCBridge
from test_laser import FakeCube, until

from osc_protocol import decode_packet, encode_message


class OSCProtocolTests(unittest.TestCase):
    def test_known_wire_message(self):
        packet = b'/test\0\0\0,if\0' + struct.pack('>if', 7, .5)
        self.assertEqual(decode_packet(packet), [('/test', [7, .5])])
        self.assertEqual(encode_message('/test', [7, .5]), packet)

    def test_strings_and_boolean_tags(self):
        message = encode_message('/laser/shape', ['eight', True, False])
        self.assertEqual(decode_packet(message), [('/laser/shape', ['eight', True, False])])

    def test_immediate_bundle(self):
        first = encode_message('/laser/brightness', [.5])
        second = encode_message('/laser/shape', ['circle'])
        packet = b'#bundle\0' + struct.pack('>Q', 1)
        for message in [first, second]:
            packet += struct.pack('>i', len(message)) + message
        self.assertEqual(decode_packet(packet), [('/laser/brightness', [.5]), ('/laser/shape', ['circle'])])

    def test_future_bundle_is_not_executed_early(self):
        future = int(time.time() + 2208988800 + 60) << 32
        packet = b'#bundle\0' + struct.pack('>Q', future)
        with self.assertRaisesRegex(ValueError, '未来時刻'):
            decode_packet(packet)

    def test_malformed_packets_are_rejected(self):
        for packet in [b'', b'/bad', b'/bad\0\0\0\0,f\0\0', b'#bundle\0' + b'\0' * 8 + struct.pack('>i', 100)]:
            with self.assertRaises(ValueError):
                decode_packet(packet)

    def test_nonfinite_numbers_are_rejected(self):
        with self.assertRaises(ValueError):
            encode_message('/test', [float('nan')])


class OSCBridgeTests(unittest.TestCase):
    def setUp(self):
        self.cube = FakeCube()
        self.laser = LaserBridge('127.0.0.1', *(sock.getsockname()[1] for sock in self.cube.sockets))
        self.assertTrue(until(lambda: self.laser.snapshot()['connected']))
        self.osc = OSCBridge(self.laser)
        self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.client.bind(('127.0.0.1', 0))
        self.client.settimeout(.3)

    def tearDown(self):
        self.osc.close()
        self.laser.close()
        self.cube.close()
        self.client.close()

    def enable_receive(self):
        temporary = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        temporary.bind(('127.0.0.1', 0))
        self.port = temporary.getsockname()[1]
        temporary.close()
        self.osc.configure({'rx_enabled': True, 'rx_port': self.port})

    def send(self, address, args=()):
        self.client.sendto(encode_message(address, args), ('127.0.0.1', self.port))

    def test_receive_changes_parameters_over_udp(self):
        self.enable_receive()
        self.send('/laser/speed', [.65])
        self.assertTrue(until(lambda: abs(self.laser.snapshot()['params']['speed'] - .65) < .001))
        self.send('/laser/brightness', [1.0])
        self.assertTrue(until(lambda: self.laser.snapshot()['params']['brightness'] == 5))
        self.send('/laser/shape', ['eight'])
        self.assertTrue(until(lambda: self.laser.snapshot()['params']['shape'] == 'eight'))
        self.assertEqual(self.osc.snapshot()['received'], 3)

    def test_osc_density_controls_hand_count(self):
        self.enable_receive()
        self.send('/laser/shape', ['hands'])
        self.assertTrue(until(lambda: self.laser.snapshot()['params']['shape'] == 'hands'))
        self.send('/laser/density', [1.0])
        self.assertTrue(until(lambda: self.laser.snapshot()['params']['density'] == 8))
        self.send('/laser/density', [0.0])
        self.assertTrue(until(lambda: self.laser.snapshot()['params']['density'] == 1))

    def test_incoming_osc_cannot_arm_real_output(self):
        self.enable_receive()
        self.laser.action('/api/mode', {'mode': 'real'})
        self.send('/laser/arm', [True])
        self.assertTrue(until(lambda: bool(self.osc.snapshot()['error'])))
        self.send('/laser/start')
        self.assertTrue(until(lambda: '許可' in self.osc.snapshot()['error']))
        self.assertFalse(self.laser.snapshot()['armed'])
        self.assertFalse(self.laser.snapshot()['running'])
        self.assertNotIn(b'\x80\x01', self.cube.packets)

    def test_demo_start_stop_and_bad_packet_recovery(self):
        self.enable_receive()
        self.client.sendto(b'bad!', ('127.0.0.1', self.port))
        self.assertTrue(until(lambda: bool(self.osc.snapshot()['error'])))
        self.send('/laser/start')
        self.assertTrue(until(lambda: self.laser.snapshot()['running']))
        self.send('/laser/stop')
        self.assertTrue(until(lambda: not self.laser.snapshot()['running']))
        self.assertTrue(all(packet == b'\x77' for packet in self.cube.packets))

    def test_midi_is_encoded_as_osc_and_noteoff_is_zero(self):
        port = self.client.getsockname()[1]
        self.osc.configure({'tx_enabled': True, 'tx_port': port})
        self.osc.forward_midi({'status': 0xb2, 'index': 7, 'value': 127})
        packet, _ = self.client.recvfrom(2048)
        self.assertEqual(decode_packet(packet), [('/apc40/cc/3/7', [1.0])])
        self.osc.forward_midi({'status': 0x82, 'index': 53, 'value': 64})
        packet, _ = self.client.recvfrom(2048)
        self.assertEqual(decode_packet(packet), [('/apc40/note/3/53', [0.0])])
        self.assertEqual(self.osc.snapshot()['sent'], 2)

    def test_port_conflict_preserves_previous_configuration(self):
        before = self.osc.snapshot()
        with self.assertRaises(RuntimeError):
            self.osc.configure({'rx_enabled': True, 'rx_port': self.client.getsockname()[1]})
        after = self.osc.snapshot()
        self.assertEqual(before['rx_enabled'], after['rx_enabled'])
        self.assertEqual(before['rx_port'], after['rx_port'])

    def test_sender_is_off_by_default(self):
        self.osc.forward_midi({'status': 0xb0, 'index': 7, 'value': 127})
        self.assertEqual(self.osc.snapshot()['sent'], 0)
        with self.assertRaises(ValueError):
            self.osc.forward_midi({'status': 0x1b0, 'index': 7, 'value': 127})


if __name__ == '__main__':
    unittest.main()
