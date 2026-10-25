import json
import math
import struct
from pathlib import Path
import tempfile
import unittest

from laser import LaserBridge, encode_samples, make_frame
from svg_import import import_svg, path_outlines, transform, apply, svg_frame

SIMPLE = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0 L100 0 L100 100 Z"/></svg>'
ANIMATED = '''<svg xmlns="http://www.w3.org/2000/svg"><style>
.f{visibility:hidden}.f0{animation:f0 2s step-end infinite}.f1{animation:f1 2s step-end infinite}
@keyframes f0{0%{visibility:visible}25%{visibility:hidden}}
@keyframes f1{0%{visibility:hidden}25%{visibility:visible}100%{visibility:hidden}}
</style><g class="f f0"><path d="M0 0L100 0"/></g><g class="f f1"><path d="M0 100L100 100"/></g></svg>'''


class SVGTests(unittest.TestCase):
    def test_relative_subpaths_and_closed_continuation(self):
        paths = path_outlines('m10 10 10 0 0 10z m40 0 h10 v10z')
        self.assertEqual(paths[0], [(10,10), (20,10), (20,20), (10,10)])
        self.assertEqual(paths[1], [(50,10), (60,10), (60,20), (50,10)])
        self.assertEqual(path_outlines('M0 0 L10 10z L20 20')[-1], [(0,0), (20,20)])

    def test_curves_and_arcs_have_correct_endpoints(self):
        for data in ('M0 0 C0 10 10 10 10 0 S20 -10 20 0',
                     'M0 0 Q5 10 10 0 T20 0', 'M0 0 A10 10 35 0 1 20 0'):
            paths = path_outlines(data)
            self.assertEqual(paths[0][0], (0,0)); self.assertEqual(paths[0][-1], (20,0))
            self.assertGreater(len(paths[0]), 15)
        small_arc = path_outlines('M0 0 a1 1 0 1 1 20 0')[0]
        self.assertTrue(all(math.isfinite(v) for p in small_arc for v in p))
        for data in ('M0 0 A1 1 0 2 1 20 0', 'M0 0 C10 2', 'MNaN 0', 'M1e999 0', 'L1 2 garbage'):
            with self.assertRaises(ValueError): path_outlines(data)

    def test_transform_order_and_rotation_centre(self):
        self.assertEqual(apply(transform('translate(10,20) scale(2)'), (1,2)), (12,24))
        x,y = apply(transform('rotate(90 10 10)'), (20,10))
        self.assertAlmostEqual(x,10); self.assertAlmostEqual(y,20)

    def test_primitives_and_white_background(self):
        source = '''<svg><rect width="100" height="100" fill="white"/><g transform="translate(2,3)" fill="none" stroke="red"><rect width="20" height="30" rx="3"/><circle cx="20" cy="20" r="10"/><ellipse rx="20" ry="10"/><line x1="0" x2="80"/><polygon points="0,0 5,10 10,0"/><polyline points="0,0 10,5"/></g></svg>'''
        asset = import_svg(source)
        self.assertEqual(asset['max_paths'], 6)
        self.assertEqual(import_svg(source, ignore_pale=False)['max_paths'], 7)
        self.assertLessEqual(asset['max_points'], 800)

    def test_frame_timing_and_blank_travel(self):
        asset = import_svg(ANIMATED)
        self.assertEqual(len(asset['frames']), 2)
        self.assertEqual(asset['starts'], [0, .25]);self.assertEqual(asset['duration'], 2)
        params = {'shape':'svg','scale':30,'brightness':5,'colour':'#ff0000','speed':1}
        first = svg_frame(asset,params,.2); second = svg_frame(asset,params,.6)
        self.assertNotEqual(first, second)
        self.assertEqual(first, svg_frame(asset,params,2.2))
        self.assertTrue(all(p[2:] == (0,0,0) for p in first[:12]+first[-4:]))
        self.assertTrue(all(p[2:] == (0,0,0) for p in svg_frame(asset,params,.2,True)))
        self.assertTrue(all(0 <= v <= 4095 for p in first for v in p))
        self.assertLessEqual(max(p[2] for p in first),205)
        self.assertEqual(make_frame(params,.6,False,asset), second)

    def test_blank_animation_frame_stays_dark(self):
        asset = import_svg('<svg><g class="f f0"/><g class="f f1"><path d="M0 0L100 100"/></g></svg>')
        points = svg_frame(asset,{'scale':20,'brightness':5,'colour':'#ff0000'},0)
        self.assertTrue(all(p[2:] == (0,0,0) for p in points))

    def test_downward_svg_motion_maps_to_downward_projector_motion(self):
        asset = import_svg(ANIMATED)
        params = {'scale': 30, 'brightness': 1, 'colour': '#ff0000'}
        upper = [p for p in struct.iter_unpack('<5H', encode_samples(svg_frame(asset, params, .2))) if any(p[2:])]
        lower = [p for p in struct.iter_unpack('<5H', encode_samples(svg_frame(asset, params, .6))) if any(p[2:])]
        # SVG/canvas Y increases downward; scanner Y increases upward.
        self.assertGreater(upper[0][1], lower[0][1])
        self.assertEqual([p[0] for p in upper], [p[0] for p in lower])
        self.assertTrue(all(point[1] > 2048 for point in upper))
        self.assertTrue(all(point[1] < 2048 for point in lower))

    def test_active_content_ignored_and_entities_rejected(self):
        asset = import_svg('<svg><script>raise Error()</script><image href="https://example.invalid/x"/><path onload="alert(1)" d="M0 0L100 100"/></svg>')
        self.assertEqual(asset['max_paths'],1)
        self.assertTrue(any('script' in warning for warning in asset['warnings']))
        for source in ('<!DOCTYPE svg [<!ENTITY x "test">]><svg/>', '<svg><path d="M0 0"/></svg>', '<svg>'+(' '*2097152)+'</svg>', '<html/>'):
            with self.assertRaises(ValueError): import_svg(source)

    def test_crowded_svg_is_limited_and_subpaths_are_blanked(self):
        source = '<svg>'+''.join('<path d="M%d 0L%d 100"/>' % (i,i) for i in range(80))+'</svg>'
        asset = import_svg(source)
        self.assertEqual(asset['max_paths'],24);self.assertLessEqual(asset['max_points'],800)
        points = svg_frame(asset,{'scale':30,'brightness':5,'colour':'#ff0000'},0)
        offset = 0
        for path in asset['frames'][0]:
            self.assertTrue(all(p[2:] == (0,0,0) for p in points[offset:offset+12]))
            offset += len(path)+16
            self.assertTrue(all(p[2:] == (0,0,0) for p in points[offset-4:offset]))
        self.assertEqual(offset,len(points))

    def test_persistence_and_upload_never_arm_or_start(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'svg.json'
            bridge = LaserBridge('127.0.0.2', svg_path=path)
            try:
                with self.assertRaisesRegex(ValueError,'先にSVG'):bridge.action('/api/params', {'shape':'svg'})
                bridge.action('/api/start',{})
                result = bridge.install_svg({'source':SIMPLE,'name':'drawing.svg','ignore_pale':True})
                self.assertFalse(result['running']);self.assertFalse(result['armed'])
                self.assertEqual(result['params']['shape'],'svg')
                before = bridge.svg_asset()
                with self.assertRaises(ValueError):bridge.install_svg({'source':'bad svg'})
                self.assertIs(bridge.svg_asset(), before)
                self.assertEqual(json.loads(path.read_text())['source'],SIMPLE)
            finally: bridge.close()
            restored = LaserBridge('127.0.0.2', svg_path=path)
            try:
                state=restored.snapshot()
                self.assertEqual(state['mode'],'demo');self.assertFalse(state['armed']);self.assertFalse(state['running'])
                self.assertEqual(state['svg']['name'],'drawing.svg')
            finally:restored.close()


if __name__ == '__main__':unittest.main()
