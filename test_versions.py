import copy
import json
from pathlib import Path
import unittest

from horror import horror_frame, scene_plan
from laser import LaserBridge
from show_profiles import catalog, scene_for, preset_for
from svg_import import import_svg
from test_laser import FakeCube,until


class VersionTests(unittest.TestCase):
    def test_v1_scene_is_preserved_and_v2_is_separate(self):
        original=json.loads((Path(__file__).parent/'static'/'horror-scene.json').read_text())
        self.assertEqual(scene_for('v1'),original)
        self.assertIsNot(scene_for('v1'),scene_for('v2'))
        self.assertEqual(scene_for('v2')['mode'],'separate')
        self.assertGreater(scene_plan(75,'v1')['small_blood'],0)
        self.assertEqual(scene_plan(75,'v2')['small_blood'],0)
        self.assertEqual(scene_plan(0,'v1')['fans'],3)
        self.assertEqual(scene_plan(0,'v2')['fans'],0)
        changed=catalog();changed['shows'][0]['scene']['horror_at']=10
        self.assertEqual(scene_for('v1')['horror_at'],90)

    def test_v2_has_strictly_separate_hands_and_blood_phases(self):
        for value in (100,95,90,89.999,80,60,50,49.999,25,0):
            plan=scene_plan(value,'v2')
            self.assertFalse(plan['hands'] and plan['large_blood'])
            self.assertEqual(plan['small_blood'],0);self.assertEqual(plan['fans'],0)
        self.assertEqual(scene_plan(90,'v2')['stage'],'hearts')
        self.assertEqual(scene_plan(50,'v2')['hands'],24)
        self.assertEqual(scene_plan(50,'v2')['large_blood'],0)
        self.assertEqual(scene_plan(49.999,'v2')['hands'],0)
        self.assertEqual(scene_plan(49.999,'v2')['large_blood'],1)
        counts=[scene_plan(v,'v2')['hands'] for v in (89.99,80,70,60,50)]
        self.assertEqual(counts,sorted(counts))

    def test_v2_output_is_hands_only_then_blood_only(self):
        params=preset_for('v2');svg=import_svg('<svg><path d="M0 0L100 0L25 100Z"/></svg>')
        params['gauge']['value']=75
        self.assertEqual(horror_frame(params,2,svg),horror_frame(params,2))
        for value in (49,25,0):
            params['gauge']['value']=value
            self.assertTrue(all(p[2:]==(0,0,0) for p in horror_frame(params,2)))
            self.assertTrue(any(any(p[2:]) for p in horror_frame(params,2,svg)))
            self.assertEqual(horror_frame(params,2,svg,fan=svg,fan_elapsed=5),horror_frame(params,2,svg))

    def test_switch_restores_each_version_settings_and_stops_output(self):
        cube=FakeCube();bridge=LaserBridge('127.0.0.1',*(s.getsockname()[1] for s in cube.sockets))
        try:
            self.assertTrue(until(lambda:bridge.snapshot()['connected']))
            bridge.action('/api/params',{'brightness':2,'gauge':{'reactive':True,'value':75}})
            first=bridge.action('/api/version',{'version':'v2'})
            self.assertEqual(first['params']['show_version'],'v2')
            self.assertEqual(first['scene']['stage'],'hands')
            bridge.action('/api/params',{'brightness':4,'gauge':{'value':30}})
            bridge.action('/api/arm',{'acknowledge':True})
            bridge.action('/api/start',{})
            restored=bridge.action('/api/version',{'version':'v1'})
            self.assertFalse(restored['running']);self.assertFalse(restored['armed'])
            self.assertEqual(restored['params']['brightness'],2)
            self.assertEqual(restored['params']['gauge']['value'],75)
            second=bridge.action('/api/version',{'version':'v2'})
            self.assertEqual(second['params']['brightness'],4)
            self.assertEqual(second['params']['gauge']['value'],30)
            self.assertEqual(second['scene']['stage'],'blood')
            before=copy.deepcopy(second['params'])
            for bad in (None,True,3,'v3',[],{}):
                with self.assertRaises(ValueError):bridge.action('/api/version',{'version':bad})
            self.assertEqual(bridge.snapshot()['params'],before)
        finally:bridge.close();cube.close()


if __name__=='__main__':unittest.main()
