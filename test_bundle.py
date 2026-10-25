"""Verify that a clone can reproduce the show without any local runtime data."""
import json
from pathlib import Path
import tempfile
import unittest

from laser import LaserBridge
from horror import horror_frame, scene_plan
from meter import overlay_meter
from show_profiles import preset_for

ROOT=Path(__file__).resolve().parent


class BundleTests(unittest.TestCase):
    def test_bundled_assets_and_preset_work_without_runtime_files(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'missing-runtime'
            bridge=LaserBridge('127.0.0.2',svg_path=runtime/'imported-svg.json',asset_dir=ROOT/'assets')
            try:
                params=preset_for('v1')
                state=bridge.action('/api/params',params)
                self.assertEqual(state['mode'],'demo')
                self.assertFalse(state['running']);self.assertFalse(state['armed'])
                self.assertEqual(state['params']['gauge']['value'],100)
                self.assertTrue(state['params']['gauge']['reactive'])
                self.assertEqual(state['effects']['blood']['frame_count'],61)
                self.assertEqual(state['effects']['fan']['frame_count'],150)
                self.assertTrue(bridge.effects['fan']['fan_eye_emphasis'])
                self.assertFalse(runtime.exists())
                for value in (100,95,90,89.99,50,49.99,0):
                    bridge.action('/api/params',{'gauge':{'value':value}})
                    frame=overlay_meter(horror_frame(bridge.params,2.5,bridge.effects['blood'],fan=bridge.effects['fan'],fan_elapsed=5),bridge.params)
                    self.assertLessEqual(len(frame),1200)
                    self.assertTrue(all(0<=v<=4095 for point in frame for v in point))
                self.assertEqual(scene_plan(0)['fans'],3)
                self.assertEqual(scene_plan(90)['hands'],0)
            finally:bridge.close()
