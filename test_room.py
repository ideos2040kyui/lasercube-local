import json
from pathlib import Path
import tempfile
import unittest

from horror import horror_frame
from meter import overlay_meter
from room_import import convert_room_decoded
from show_profiles import preset_for
from svg_import import svg_frame


class RoomTests(unittest.TestCase):
    def fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);frames=[];w=h=40
            for shift in (0,1):
                data=bytearray([245,245,245,255]*(w*h))
                for x1,y1,x2,y2,colour in ((7+shift,6,32+shift,32,(0,0,0)),
                                          (10+shift,10,18+shift,28,(220,0,0)),
                                          (22+shift,10,30+shift,28,(0,40,170)),
                                          (36,36,39,39,(100,100,100))):
                    for y in range(y1,y2):
                        for x in range(x1,x2):
                            offset=(y*w+x)*4;data[offset:offset+4]=bytes((*colour,255))
                frames.append(data)
            (root/'frames.rgba').write_bytes(b''.join(frames))
            (root/'metadata.json').write_text(json.dumps({'width':w,'height':h,'frames':2,'delays':[.1,.2]}))
            return convert_room_decoded(root,'room.gif')

    def test_room_retains_animation_colours_and_ignores_isolated_lettering(self):
        room=self.fixture()
        self.assertAlmostEqual(room['duration'],.3)
        self.assertAlmostEqual(room['starts'][1],1/3)
        self.assertEqual(room['frame_colours'],[['#d9dded','#ed1c24','#245bff']]*2)
        self.assertEqual([len(frame) for frame in room['frames']],[3,3])
        self.assertNotEqual(room['frames'][0],room['frames'][1])
        self.assertLessEqual(room['max_points'],640)
        # The far-away lettering would move the room's centred bounding box.
        outline=room['frames'][0][0]
        self.assertLess(max(x for x,y in outline),.9)
        params=preset_for('v2');frame=svg_frame(room,params,.05)
        self.assertTrue(any(r>g*2 and r>b*2 for x,y,r,g,b in frame))
        self.assertTrue(any(b>r*2 and b>g*2 for x,y,r,g,b in frame))
        self.assertTrue(all(not any(p[2:]) for p in svg_frame(room,params,.05,blank=True)))

    def test_room_is_present_in_every_v2_stage_and_v1_is_unchanged(self):
        room=self.fixture()
        for version in ('v1','v2'):
            params=preset_for(version)
            for value in (100,95,90,89.999,50,49.999,0):
                params['gauge']['value']=value
                base=horror_frame(params,.05)
                with_room=horror_frame(params,.05,room=room)
                if version=='v1':self.assertEqual(base,with_room)
                else:
                    self.assertNotEqual(base,with_room)
                    self.assertTrue(any(b>r*2 and b>g*2 for x,y,r,g,b in with_room))
                self.assertTrue(all(not any(p[2:]) for p in horror_frame(params,.05,room=room,blank=True)))
            params['brightness']=0
            self.assertTrue(all(not any(p[2:]) for p in horror_frame(params,.05,room=room)))

    def test_bundled_room_stays_within_scanner_budget_with_blood_and_hands(self):
        root=Path(__file__).parent/'assets'
        room=json.loads((root/'effect-room.json').read_text());blood=json.loads((root/'effect-blood.json').read_text())
        params=preset_for('v2')
        self.assertEqual(len(room['frames']),60);self.assertAlmostEqual(room['duration'],4)
        for value in (100,90,89,75,50,49,25,0):
            params['gauge']['value']=value
            for elapsed in (0,.5,1.5,2,3.9,4):
                frame=overlay_meter(horror_frame(params,elapsed,blood,room=room),params)
                self.assertLessEqual(len(frame),1200)
                self.assertTrue(all(0<=c<=4095 for point in frame for c in point))


if __name__=='__main__':unittest.main()
