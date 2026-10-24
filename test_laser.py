import math
import select
import socket
import struct
import threading
import time
import unittest

from laser import LaserBridge, decode_info, make_frame

# Captured protocol fixture with the hardware MAC replaced by a local dummy address.
CAPTURED = bytes.fromhex('7700000010070000ff0fb8880000b8880000031f127017ff4300020000000001c0a80101020357696669204c617365724375626520322e35572050726f000000')


class FakeCube:
    def __init__(self):
        self.packets = []
        self.info = bytearray(CAPTURED)
        self.info[5] = 2
        self.info[24] = 25
        self.stop_event = threading.Event()
        self.sockets = []
        for _ in range(2):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(('127.0.0.1', 0))
            self.sockets.append(sock)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            ready, _, _ = select.select(self.sockets, [], [], .02)
            for sock in ready:
                packet, peer = sock.recvfrom(2048)
                self.packets.append(packet)
                if packet == b'\x77':
                    sock.sendto(self.info, peer)

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=1)
        for sock in self.sockets:
            sock.close()


def until(predicate, timeout=1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.01)
    return False


class ProtocolTests(unittest.TestCase):
    def test_captured_real_information_and_warning(self):
        info = decode_info(CAPTURED)
        self.assertEqual(info['model'], 'Wifi LaserCube 2.5W Pro')
        self.assertEqual(info['firmware'], '0.16')
        self.assertEqual(info['temperature'], 67)
        self.assertTrue(info['temperature_warning'])
        self.assertEqual(info['max_dac_rate'], 35000)
        self.assertIsNone(info['battery'])
        with self.assertRaises(ValueError):
            decode_info(CAPTURED[:-1])

    def test_moving_frames_are_bounded_and_end_blank(self):
        for shape in ('circle', 'eight', 'triangle'):
            frame = make_frame({'shape': shape, 'scale': 30, 'brightness': 5, 'colour': '#ffffff'}, .3)
            self.assertEqual(len(frame), 300)
            self.assertTrue(all(point[2:] == (0, 0, 0) for point in frame[:8] + frame[-8:]))
            self.assertTrue(all(0 <= value <= 4095 for point in frame for value in point))
            self.assertGreater(len(set((p[0], p[1]) for p in frame)), 200)
            self.assertLessEqual(max(p[2] for p in frame), 205)

    def test_hand_density_adds_separately_blanked_outlines(self):
        params = {'shape': 'hands', 'scale': 30, 'brightness': 5, 'colour': '#ed1c24', 'density': 8}
        frame = make_frame(params, .4)
        self.assertEqual(len(frame), 800)
        for offset in range(0, 800, 100):
            hand = frame[offset:offset + 100]
            self.assertTrue(all(point[2:] == (0, 0, 0) for point in hand[:8] + hand[-8:]))
            self.assertGreater(len(set((p[0], p[1]) for p in hand[8:-8])), 70)
        self.assertTrue(all(0 <= value <= 4095 for point in frame for value in point))
        self.assertLessEqual(max(p[2] for p in frame), 205)
        self.assertNotEqual(frame[8:92], frame[108:192])

    def test_hands_move_and_blank_frames_are_dark(self):
        params = {'shape': 'hands', 'scale': 20, 'brightness': 2, 'colour': '#ed1c24', 'density': 4}
        self.assertNotEqual(make_frame(params, 0), make_frame(params, .6))
        self.assertTrue(all(p[2:] == (0, 0, 0) for p in make_frame(params, .6, blank=True)))


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.cube = FakeCube()
        self.bridge = LaserBridge('127.0.0.1', *(sock.getsockname()[1] for sock in self.cube.sockets))
        self.assertTrue(until(lambda: self.bridge.snapshot()['connected']))

    def tearDown(self):
        self.bridge.close()
        self.cube.close()

    def test_discovery_and_demo_never_send_output(self):
        self.bridge.action('/api/start', {})
        self.bridge.action('/api/params', {'shape': 'eight', 'brightness': 3})
        self.bridge.action('/api/stop', {})
        time.sleep(.04)
        self.assertTrue(all(packet == b'\x77' for packet in self.cube.packets))

    def test_real_output_requires_explicit_arm(self):
        self.bridge.action('/api/mode', {'mode': 'real'})
        with self.assertRaises(RuntimeError):
            self.bridge.action('/api/start', {})
        with self.assertRaises(ValueError):
            self.bridge.action('/api/arm', {'acknowledge': False})

    def test_temperature_warning_is_visible_but_does_not_prevent_arm(self):
        self.bridge.action('/api/mode', {'mode': 'real'})
        with self.bridge.lock:
            self.bridge.info['temperature_warning'] = True
        self.bridge.action('/api/arm', {'acknowledge': True})
        state = self.bridge.snapshot()
        self.assertTrue(state['armed'])
        self.assertTrue(state['device']['temperature_warning'])
        self.assertFalse(state['running'])
        self.assertNotIn(b'\x80\x01', self.cube.packets)

    def test_over_temperature_still_blocks_output(self):
        self.bridge.action('/api/mode', {'mode': 'real'})
        with self.bridge.lock:
            self.bridge.info['over_temperature'] = True
        with self.assertRaisesRegex(RuntimeError, '過熱停止'):
            self.bridge.action('/api/arm', {'acknowledge': True})
        self.assertNotIn(b'\x80\x01', self.cube.packets)

    def test_over_temperature_during_stream_stops_output(self):
        self.bridge.action('/api/mode', {'mode': 'real'})
        self.bridge.action('/api/arm', {'acknowledge': True})
        self.bridge.action('/api/start', {})
        with self.bridge.lock:
            self.bridge.info['over_temperature'] = True
        self.assertTrue(until(lambda: not self.bridge.snapshot()['running']))
        self.assertTrue(until(lambda: self.cube.packets.count(b'\x80\x00') >= 3))

    def test_invalid_params_do_not_change_settings(self):
        before = self.bridge.snapshot()['params']
        for value in (float('nan'), float('inf'), -1, 101):
            with self.assertRaises(ValueError):
                self.bridge.action('/api/params', {'brightness': value})
        self.assertEqual(self.bridge.snapshot()['params'], before)

    def controlled_clock(self):
        clock = [time.monotonic()]
        with self.bridge.lock:
            self.bridge.clock = lambda: clock[0]
        return clock

    def advance_with_heartbeat(self, clock, seconds):
        with self.bridge.lock:
            clock[0] += seconds
            self.bridge.action('/api/heartbeat', {})

    def test_continuous_output_runs_beyond_old_limit_until_stopped(self):
        clock = self.controlled_clock()
        self.bridge.action('/api/start', {})
        self.advance_with_heartbeat(clock, 90)
        time.sleep(.04)
        self.assertTrue(self.bridge.thread.is_alive())
        self.assertTrue(self.bridge.snapshot()['running'])
        self.assertIsNone(self.bridge.snapshot()['remaining'])
        self.bridge.action('/api/stop', {})
        self.assertFalse(self.bridge.snapshot()['running'])

    def test_optional_timer_still_stops_at_duration(self):
        clock = self.controlled_clock()
        self.bridge.action('/api/params', {'duration': 5})
        self.bridge.action('/api/start', {})
        self.advance_with_heartbeat(clock, 6)
        self.assertTrue(until(lambda: not self.bridge.snapshot()['running']))
        self.assertTrue(any('指定時間' in e['message'] for e in self.bridge.snapshot()['events']))

    def test_switching_to_continuous_while_running_cancels_timer(self):
        clock = self.controlled_clock()
        self.bridge.action('/api/params', {'duration': 5})
        self.bridge.action('/api/start', {})
        self.advance_with_heartbeat(clock, 4)
        self.bridge.action('/api/params', {'duration': 0})
        self.advance_with_heartbeat(clock, 90)
        time.sleep(.04)
        self.assertTrue(self.bridge.snapshot()['running'])
        self.assertIsNone(self.bridge.snapshot()['remaining'])

    def test_other_parameter_changes_do_not_extend_selected_timer(self):
        clock = self.controlled_clock()
        self.bridge.action('/api/params', {'duration': 5})
        self.bridge.action('/api/start', {})
        self.advance_with_heartbeat(clock, 4)
        self.bridge.action('/api/params', {'duration': 5, 'brightness': 2})
        self.advance_with_heartbeat(clock, 2)
        self.assertTrue(until(lambda: not self.bridge.snapshot()['running']))

    def test_invalid_duration_does_not_change_settings(self):
        for value in (True, -1, 1, 4, 31, float('nan')):
            with self.assertRaises(ValueError):
                self.bridge.action('/api/params', {'duration': value})
        self.assertEqual(self.bridge.snapshot()['params']['duration'], 0)

    def test_hand_preset_and_density_validation(self):
        self.bridge.action('/api/params', {'shape': 'hands', 'density': 8})
        state = self.bridge.snapshot()
        self.assertEqual(state['params']['colour'], '#ed1c24')
        self.assertEqual(state['params']['density'], 8)
        for value in (0, 9, 1.5, True, float('nan')):
            with self.assertRaises(ValueError):
                self.bridge.action('/api/params', {'density': value})
        self.assertEqual(self.bridge.snapshot()['params']['density'], 8)

    def test_unexpected_stream_error_stops_and_blocks_restart(self):
        self.bridge.action('/api/mode', {'mode': 'real'})
        self.bridge.action('/api/arm', {'acknowledge': True})
        with self.bridge.lock:
            self.bridge.action('/api/start', {})
            def broken_frame(*args, **kwargs):
                raise ValueError('test stream failure')
            self.bridge._send_frame = broken_frame
        self.assertTrue(until(lambda: not self.bridge.snapshot()['running']))
        self.assertTrue(until(lambda: not self.bridge.thread.is_alive()))
        with self.assertRaisesRegex(RuntimeError, '再起動'):
            self.bridge.action('/api/start', {})
        self.assertTrue(until(lambda: self.cube.packets.count(b'\x80\x00') >= 3))

    def test_real_stream_and_stop_use_correct_packets(self):
        self.bridge.action('/api/mode', {'mode': 'real'})
        self.bridge.action('/api/arm', {'acknowledge': True})
        self.bridge.action('/api/start', {})
        self.assertTrue(until(lambda: sum(p.startswith(b'\xa9') for p in self.cube.packets) >= 6))
        self.bridge.action('/api/stop', {})
        self.assertTrue(until(lambda: self.cube.packets.count(b'\x80\x00') >= 3))
        packets = [p for p in self.cube.packets if p.startswith(b'\xa9')]
        self.assertTrue(all(len(p) == 1004 for p in packets))
        self.assertIn(b'\x82' + struct.pack('<I', 12000), self.cube.packets)
        self.assertFalse(self.bridge.snapshot()['running'])
        self.assertFalse(self.bridge.snapshot()['armed'])

    def test_lost_browser_heartbeat_stops_output(self):
        self.bridge.action('/api/mode', {'mode': 'real'})
        self.bridge.action('/api/arm', {'acknowledge': True})
        self.bridge.action('/api/start', {})
        self.assertTrue(until(lambda: not self.bridge.snapshot()['running'], timeout=3.5))
        self.assertFalse(self.bridge.snapshot()['armed'])
        self.assertTrue(any('通信が途切れ' in e['message'] for e in self.bridge.snapshot()['events']))


if __name__ == '__main__':
    unittest.main()
