import json
from pathlib import Path
import tempfile
import unittest
from gif_import import convert_decoded, trace_mask
from svg_import import svg_frame
from horror import horror_frame, scene_plan
from test_layers import parameters
from test_laser import FakeCube, until
from laser import LaserBridge


class GIFTests(unittest.TestCase):
    def asset(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);w,h=16,10;frames=[]
            for intensity in (0,128,255):
                pixels=bytearray([255,255,255,255]*(w*h))
                if intensity<255:
                    for y in range(2,8):
                        for x in range(3,13):
                            p=(y*w+x)*4;pixels[p:p+4]=bytes((intensity,intensity,intensity,255))
                frames.append(pixels)
            (root/'frames.rgba').write_bytes(b''.join(frames))
            (root/'metadata.json').write_text(json.dumps({'width':w,'height':h,'frames':3,'delays':[.1,.2,.1]}))
            return convert_decoded(root,'fixture.gif')

    def test_gif_retains_timing_empty_frames_and_fade(self):
        asset=self.asset()
        self.assertEqual(len(asset['frames']),3)
        self.assertEqual(asset['starts'],[0,.25,.7500000000000001])
        self.assertAlmostEqual(asset['duration'],.4)
        self.assertEqual(asset['frames'][0],asset['frames'][1])
        self.assertEqual(asset['frames'][2],[])
        self.assertEqual(asset['frame_opacity'][0],1)
        self.assertAlmostEqual(asset['frame_opacity'][1],.498,places=3)
        self.assertEqual(asset['frame_opacity'][2],0)
        params={'brightness':5,'scale':30,'colour':'#ff0000'}
        solid=svg_frame(asset,params,.05);faded=svg_frame(asset,params,.2);empty=svg_frame(asset,params,.35)
        self.assertEqual(max(p[2] for p in solid),205)
        self.assertTrue(100<=max(p[2] for p in faded)<=103)
        self.assertTrue(all(p[2:]==(0,0,0) for p in empty))
        self.assertLessEqual(asset['max_points'],800)

    def test_ring_keeps_both_outer_and_inner_contours(self):
        mask=bytearray(20*20)
        for y in range(3,17):
            for x in range(3,17):
                if x<5 or x>14 or y<5 or y>14:mask[y*20+x]=1
        paths=trace_mask(mask,20,20)
        self.assertEqual(len(paths),2)
        self.assertTrue(all(p[0]==p[-1] for p in paths))

    def test_three_fans_only_at_zero_and_the_gif_remains_animated(self):
        fan=self.asset();params=parameters();params['gauge'].update(reactive=True,value=0)
        for value in (.001,50,90,100):self.assertEqual(scene_plan(value)['fans'],0)
        self.assertEqual(scene_plan(0)['fans'],3)
        without_fan=horror_frame(params,.05)
        entered=horror_frame(params,.05,fan=fan,fan_elapsed=0)
        self.assertEqual(without_fan,entered)
        later=horror_frame(params,.05,fan=fan,fan_elapsed=2)
        self.assertNotEqual(later,without_fan)
        self.assertLessEqual(len(later),1100)
        self.assertTrue(all(0<=v<=4095 for p in later for v in p))
        params['gauge']['value']=.001
        self.assertEqual(horror_frame(params,.05,fan=fan,fan_elapsed=2),horror_frame(params,.05))

    def test_fan_eye_preparation_preserves_animation_and_adds_readable_eyes(self):
        from fan import prepare_fan_asset
        import copy
        original=self.asset();before=copy.deepcopy(original)
        fan=prepare_fan_asset(original)
        self.assertEqual(original,before)
        self.assertEqual(fan['duration'],original['duration']);self.assertEqual(fan['starts'],original['starts'])
        self.assertEqual(len(fan['frames'][0]),3)
        self.assertEqual(len(fan['frames'][1]),3)
        self.assertEqual(fan['frames'][2],[])
        eye,pupil=fan['frames'][0][1:]
        self.assertGreater(len(eye),20);self.assertEqual(pupil[0],pupil[-1])
        self.assertGreater(max(p[0] for p in eye)-min(p[0] for p in eye),.7)
        self.assertLessEqual(fan['max_points'],800)

    def test_zero_meter_reentry_restarts_fan_animation_without_starting_output(self):
        cube=FakeCube();bridge=LaserBridge('127.0.0.1',*(s.getsockname()[1] for s in cube.sockets))
        try:
            self.assertTrue(until(lambda:bridge.snapshot()['connected']))
            clock=[100.0]
            with bridge.lock:bridge.clock=lambda:clock[0]
            bridge.action('/api/params',{'gauge':{'reactive':True,'value':1}})
            clock[0]=101
            state=bridge.action('/api/params',{'gauge':{'value':0}})
            self.assertEqual(state['fan_elapsed'],0)
            clock[0]=102
            state=bridge.action('/api/params',{'gauge':{'brightness':50}})
            self.assertEqual(state['fan_elapsed'],1)
            bridge.action('/api/params',{'gauge':{'value':1}})
            clock[0]=104
            state=bridge.action('/api/params',{'gauge':{'value':0}})
            self.assertEqual(state['fan_elapsed'],0)
            self.assertFalse(state['running']);self.assertFalse(state['armed'])
        finally:bridge.close();cube.close()


if __name__=='__main__':unittest.main()
