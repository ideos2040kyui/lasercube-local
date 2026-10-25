import copy
import math
import unittest

from horror import blood_instances, horror_frame, scene_plan
from meter import overlay_meter
from svg_import import import_svg
from test_layers import parameters
from test_laser import FakeCube, until
from laser import LaserBridge


class HorrorTests(unittest.TestCase):
    def setUp(self):
        self.params=parameters()
        self.params['gauge'].update(enabled=True,reactive=True)
        self.svg=import_svg('<svg>'+''.join('<path d="M%d 0L%d 100L%d 30Z"/>'%(i,i+20,i+40) for i in range(24))+'</svg>')

    def test_exact_ninety_and_fifty_percent_boundaries(self):
        for value in (100,95,90.000001,90):
            plan=scene_plan(value)
            self.assertEqual(plan['stage'],'hearts')
            self.assertEqual((plan['hands'],plan['small_blood'],plan['large_blood']),(0,0,0))
        for value in (89.999999,80,50):
            plan=scene_plan(value)
            self.assertEqual(plan['hearts'],0)
            self.assertGreaterEqual(plan['hands'],1);self.assertGreaterEqual(plan['small_blood'],1)
            self.assertEqual(plan['large_blood'],0)
        for value in (49.999999,25,0):self.assertGreater(scene_plan(value)['large_blood'],0)
        self.assertEqual(scene_plan(0)['hands'],24)
        self.assertEqual(scene_plan(0)['large_blood'],1)

    def test_only_one_central_blood_image_and_more_peripheral_images(self):
        for value in (49,25,0):
            plan=scene_plan(value);images=blood_instances(plan)
            central=[p for p in images if p['contours']==2]
            peripheral=[p for p in images if p['contours']==1]
            self.assertEqual(len(central),1)
            self.assertEqual(central[0]['phase'],0)
            self.assertEqual(len(peripheral),plan['small_blood'])
            self.assertTrue(all(math.hypot(p['x'],p['y'])>.7 for p in peripheral))
        self.assertEqual(scene_plan(0)['small_blood'],8)
        from horror import SCENE
        self.assertTrue(all(math.hypot(x,y)>.8 for x,y in SCENE['heart_centres']))
        self.assertTrue(all(abs(x)>=.8 or abs(y)>=.8 for x,y in SCENE['hand_centres']))
        self.assertEqual(len(SCENE['fan_centres']),3)
        self.assertEqual([p[0] for p in SCENE['fan_centres']],[-.6,0,.6])
        self.assertTrue(all(y>.6 and size>=.3 for x,y,size in SCENE['fan_centres']))

    def test_lower_meter_increases_crowding_and_blood(self):
        plans=[scene_plan(value) for value in range(90,-1,-1)]
        for key in ('hands','small_blood','large_blood'):
            values=[p[key] for p in plans]
            self.assertEqual(values,sorted(values))
        before=copy.deepcopy(self.params)
        self.params['gauge']['value']=0
        horror_frame(self.params,2,self.svg)
        self.params['gauge']['value']=before['gauge']['value']
        self.assertEqual(self.params,before)

    def test_central_blood_only_below_fifty_and_peripheral_blood_stays_small(self):
        outside=blood_instances(scene_plan(50))
        self.assertTrue(all(abs(p['x'])>.05 or abs(p['y'])>.3 for p in outside))
        self.assertTrue(all(p['scale']<.3 for p in outside))
        inside=blood_instances(scene_plan(49))
        self.assertTrue(any(p['x']==0 and p['y']==0 and p['scale']>.7 for p in inside))

    def test_hearts_fade_between_ninety_five_and_ninety(self):
        self.assertEqual(scene_plan(100)['heart_opacity'],1)
        self.assertEqual(scene_plan(95)['heart_opacity'],1)
        self.assertAlmostEqual(scene_plan(92.5)['heart_opacity'],.55)
        self.assertAlmostEqual(scene_plan(90)['heart_opacity'],.1)
        maxima=[]
        for value in (95,92.5,90):
            self.params['gauge']['value']=value
            maxima.append(max(p[2] for p in horror_frame(self.params,0,self.svg)))
        self.assertGreater(maxima[0],maxima[1]);self.assertGreater(maxima[1],maxima[2])

    def test_hearts_are_pink_and_float(self):
        self.params['gauge']['value']=100
        first=horror_frame(self.params,0,self.svg);later=horror_frame(self.params,3,self.svg)
        self.assertNotEqual(first,later)
        lit=[p for p in first if any(p[2:])]
        self.assertTrue(lit)
        self.assertTrue(all(p[2]>p[4]>p[3]>0 for p in lit))
        self.assertTrue(all(p[4]/p[2]>.6 for p in lit))

    def test_all_scenes_keep_scan_bounds_point_budget_and_blank_transitions(self):
        for value in (100,90,80,50,49.99,25,10,0):
            with self.subTest(value=value):
                self.params['gauge']['value']=value
                frame=horror_frame(self.params,2.8,self.svg)
                self.assertLessEqual(len(frame),1000)
                output=overlay_meter(frame,self.params)
                self.assertLessEqual(len(output),1200)
                self.assertTrue(all(0<=v<=4095 for p in output for v in p))
                self.assertTrue(all(v<=205 for p in output for v in p[2:]))
                self.assertTrue(all(p[2:]==(0,0,0) for p in output[:12]+output[-4:]))
                blank=horror_frame(self.params,2.8,self.svg,blank=True)
                self.assertTrue(all(p[2:]==(0,0,0) for p in blank))

    def test_no_source_svg_still_shows_hands_and_zero_brightness_is_dark(self):
        self.params['gauge']['value']=0
        self.assertTrue(any(any(p[2:]) for p in horror_frame(self.params,0)))
        self.params['brightness']=0
        self.assertTrue(all(p[2:]==(0,0,0) for p in horror_frame(self.params,0,self.svg)))


class HorrorBridgeTests(unittest.TestCase):
    def test_meter_controls_scene_without_starting_output_or_mutating_manual_layers(self):
        cube=FakeCube();bridge=LaserBridge('127.0.0.1',*(s.getsockname()[1] for s in cube.sockets))
        try:
            self.assertTrue(until(lambda:bridge.snapshot()['connected']))
            before=bridge.snapshot()['params']['layers']
            for value,stage in ((100,'hearts'),(90,'hearts'),(50,'horror'),(49,'critical'),(0,'critical'),(95,'hearts')):
                state=bridge.action('/api/params',{'gauge':{'reactive':True,'value':value}})
                self.assertEqual(state['scene']['stage'],stage)
                self.assertFalse(state['running']);self.assertFalse(state['armed'])
                self.assertEqual(state['params']['layers'],before)
            with self.assertRaises(ValueError):bridge.action('/api/params',{'gauge':{'reactive':1}})
            state=bridge.action('/api/params',{'gauge':{'reactive':False}})
            self.assertIsNone(state['scene'])
            self.assertTrue(all(p==b'\x77' for p in cube.packets))
        finally:bridge.close();cube.close()


if __name__=='__main__':unittest.main()
