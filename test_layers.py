import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from laser import LaserBridge, make_frame
from layers import default_layers, validate_layers
from meter import default_gauge, gauge_paths, meter_frame, overlay_meter, validate_gauge
from svg_import import import_svg

SVG = '<svg><path d="M0 0L100 0L25 100Z"/></svg>'


def parameters():
    return {'shape':'layers','brightness':5,'scale':30,'speed':1,'colour':'#ffffff','density':4,'layers':default_layers(),'gauge':default_gauge()}


def coloured(frame, channel):
    return [p for p in frame if p[channel] and all(p[c]==0 for c in (2,3,4) if c!=channel)]


class LayerTests(unittest.TestCase):
    def setUp(self):
        self.params=parameters()
        self.params['layers'][0].update(shape='triangle',colour='#ff0000',speed=0)
        self.params['layers'][1].update(shape='circle',colour='#00ff00',speed=0)

    def test_two_colours_are_present_and_switches_are_independent(self):
        frame=make_frame(self.params,0)
        self.assertTrue(coloured(frame,2));self.assertTrue(coloured(frame,3))
        self.params['layers'][0]['enabled']=False
        muted=make_frame(self.params,0)
        self.assertFalse(coloured(muted,2));self.assertTrue(coloured(muted,3))
        self.params['layers'][1]['brightness']=0
        self.assertTrue(all(p[2:]==(0,0,0) for p in make_frame(self.params,0)))

    def test_position_brightness_and_speed_do_not_modify_other_layer(self):
        before=make_frame(self.params,.4)
        self.params['layers'][0].update(x=50,y=-25,brightness=25,speed=.6)
        after=make_frame(self.params,.4)
        self.assertEqual(coloured(before,3),coloured(after,3))
        self.assertNotEqual(coloured(before,2),coloured(after,2))
        self.assertLessEqual(max(p[2] for p in after),52)

    def test_each_svg_can_be_a_different_asset(self):
        self.params['layers'][0].update(shape='svg')
        self.params['layers'][1].update(shape='svg')
        assets=[import_svg(SVG),import_svg('<svg><circle r="100"/></svg>')]
        frame=make_frame(self.params,0,layer_svgs=assets)
        self.assertTrue(coloured(frame,2));self.assertTrue(coloured(frame,3))
        self.assertNotEqual([p[:2] for p in coloured(frame,2)],[p[:2] for p in coloured(frame,3)])

    def test_dense_layers_keep_bounds_and_blank_stroke_boundaries(self):
        source='<svg>'+''.join('<path d="M%d 0L%d 100L%d 50Z"/>'%(i,i+1,i+2) for i in range(24))+'</svg>'
        assets=[import_svg(source),import_svg(source)]
        for i in (0,1):self.params['layers'][i].update(shape='svg',x=100 if i else -100,y=100 if i else -100)
        frame=make_frame(self.params,.4,layer_svgs=assets)
        self.assertLessEqual(len(frame),1000)
        self.assertTrue(all(0<=v<=4095 for p in frame for v in p))
        starts=0;dark=0
        for point in frame:
            if any(point[2:]):
                if dark:self.assertGreaterEqual(dark,12);starts+=1;dark=0
            else:dark+=1
        self.assertEqual(starts,48)
        self.assertTrue(all(p[2:]==(0,0,0) for p in frame[-4:]))
        blank=make_frame(self.params,.4,True,layer_svgs=assets)
        self.assertTrue(all(p[2:]==(0,0,0) for p in blank))

    def test_validation_is_strict_and_does_not_mutate_previous(self):
        previous=default_layers();before=copy.deepcopy(previous)
        for invalid in ([{'enabled':1},{}],[{'speed':math.nan},{}],[{}, {'density':1.5}], [{'shape':'layers'},{}],[{'x':101},{}],[{}, {'brightness':-1}], [{}]):
            with self.assertRaises(ValueError):validate_layers(invalid,previous)
        self.assertEqual(previous,before)

    def test_layer_upload_persists_separately_without_starting(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'imported-svg.json';bridge=LaserBridge('127.0.0.2',svg_path=path)
            try:
                bridge.install_svg({'source':SVG,'name':'original.svg'})
                bridge.install_svg({'source':'<svg><circle r="100"/></svg>','name':'layer-b.svg','layer':1})
                state=bridge.snapshot()
                self.assertEqual(state['svg']['name'],'original.svg')
                self.assertEqual(state['layer_svgs'][1]['name'],'layer-b.svg')
                self.assertEqual(state['params']['shape'],'layers')
                self.assertFalse(state['armed']);self.assertFalse(state['running'])
                for bad in (True,-1,2,'0'):
                    with self.assertRaises(ValueError):bridge.install_svg({'source':SVG,'layer':bad})
            finally:bridge.close()
            restored=LaserBridge('127.0.0.2',svg_path=path)
            try:
                state=restored.snapshot();self.assertEqual(state['layer_svgs'][1]['name'],'layer-b.svg');self.assertEqual(state['mode'],'demo')
            finally:restored.close()


class MeterTests(unittest.TestCase):
    def setUp(self):
        self.params=parameters();self.params['gauge']['enabled']=True

    def test_fill_follows_slider_and_stays_in_upper_right(self):
        empty=gauge_paths(0);half=gauge_paths(50);full=gauge_paths(100)
        self.assertEqual(len(empty),1);self.assertEqual(len(half),2)
        self.assertEqual(empty[0],full[0])
        self.assertLess(min(p[1] for p in full[1]),min(p[1] for p in half[1]))
        self.assertTrue(all(x>2048 and y<2048 for path in full for x,y in path))

    def test_meter_shortens_a_fixed_serpentine_without_rescaling_it(self):
        full = gauge_paths(100)
        length = lambda path: sum(math.hypot(b[0]-a[0], b[1]-a[1]) for a,b in zip(path,path[1:]))
        for value in (13, 37, 62, 94):
            with self.subTest(value=value):
                shortened = gauge_paths(value)
                self.assertEqual(shortened[0], full[0])
                self.assertEqual(shortened[1][:-1], full[1][:len(shortened[1])-1])
                self.assertAlmostEqual(length(shortened[1])/length(full[1]), value/100, delta=.002)

    def test_meter_ignores_main_shape_scale_speed_and_layer_position(self):
        original=meter_frame(self.params)
        self.params.update(shape='triangle',scale=5,speed=0,colour='#00ff00')
        self.params['layers'][0].update(x=-100,y=100)
        self.assertEqual(original,meter_frame(self.params))

    def test_overlay_preserves_bounds_colour_and_blanking(self):
        base=make_frame(self.params,.5,svg=import_svg(SVG))
        output=overlay_meter(base,self.params)
        self.assertLessEqual(len(output),1200)
        self.assertTrue(all(0<=v<=4095 for p in output for v in p))
        self.assertTrue(all(v<=205 for p in output for v in p[2:]))
        self.assertTrue(all(p[2:]==(0,0,0) for p in output[:12]+output[-4:]))
        self.params['gauge']['brightness']=0
        self.assertIs(overlay_meter(base,self.params),base)

    def test_zero_global_brightness_and_hidden_meter_do_not_emit(self):
        self.params['brightness']=0
        self.assertEqual(meter_frame(self.params),[])
        self.params['brightness']=5;self.params['gauge']['enabled']=False
        self.assertEqual(meter_frame(self.params),[])

    def test_meter_turns_pink_with_hearts_and_red_for_horror(self):
        self.params['gauge']['reactive']=True
        for value in (100,90):
            self.params['gauge']['value']=value
            lit=[p for p in meter_frame(self.params) if any(p[2:])]
            self.assertTrue(lit)
            self.assertTrue(all(p[2]>p[4]>p[3]>0 for p in lit))
        self.params['gauge']['value']=89.999
        self.assertTrue(all(p[3:]==(0,0) for p in meter_frame(self.params)))

    def test_meter_validation_rejects_invalid_values(self):
        for invalid in ({'value':101},{'value':-1},{'value':math.nan},{'value':True},{'brightness':math.inf},{'enabled':1}):
            with self.assertRaises(ValueError):validate_gauge(invalid,default_gauge())


if __name__=='__main__':unittest.main()
