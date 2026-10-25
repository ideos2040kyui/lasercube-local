"""Meter-driven hearts and escalating hand/blood outlines."""
import math
from layers import combine_frames
from patterns import hand_frame
from svg_import import svg_frame

from show_profiles import scene_for

SCENE = scene_for('v1')


def scene_plan(value,version='v1'):
    scene=scene_for(version)
    if value >= scene['horror_at']:
        progress=min(1,max(0,(value-scene['horror_at'])/(scene.get('heart_fade_from',95)-scene['horror_at'])))
        opacity=scene.get('heart_min_opacity',.1)+(1-scene.get('heart_min_opacity',.1))*progress
        return {'stage':'hearts','severity':0.0,'hands':0,'small_blood':0,'large_blood':0,'hearts':len(scene['heart_centres']),
                'fans':0,'heart_opacity':opacity}
    if scene.get('mode')=='separate':
        if value>=scene['central_below']:
            severity=(scene['horror_at']-value)/(scene['horror_at']-scene['central_below'])
            return {'stage':'hands','severity':severity,'central':0,
                    'hands':1+math.floor((scene['max_hands']-1)*severity**1.5),
                    'small_blood':0,'large_blood':0,'hearts':0,'fans':0}
        depth=(scene['central_below']-value)/scene['central_below']
        return {'stage':'blood','severity':depth,'central':depth,'hands':0,
                'small_blood':0,'large_blood':1,'hearts':0,'fans':0}
    severity=(scene['horror_at']-value)/scene['horror_at']
    central=max(0,(scene['central_below']-value)/scene['central_below'])
    return {'stage':'horror' if value>=scene['central_below'] else 'critical',
            'severity':severity,'central':central,
            'hands':1+math.floor((scene['max_hands']-1)*severity**1.5),
            'small_blood':1+math.floor((len(scene['blood_centres'])-1)*severity),
            'large_blood':1 if value<scene['central_below'] else 0,
            'hearts':0,'fans':len(scene.get('fan_centres',[])) if value<=0 else 0}


def blood_instances(plan,version='v1'):
    scene=scene_for(version)
    severity=plan['severity'];result=[]
    for index in range(plan['small_blood']):
        x,y=scene['blood_centres'][index]
        result.append({'x':x,'y':y,'scale':.14+.10*severity,'brightness':.25+.65*severity,'contours':1,'phase':index*.19})
    for index in range(plan['large_blood']):
        depth=plan['central']
        result.append({'x':0,'y':-.25 if plan['fans'] else 0,'scale':(.72+.28*depth)*(.8 if plan['fans'] else 1),
                       'brightness':.45+.55*depth,'contours':2,'phase':0})
    return result


def heart_frame(params,elapsed):
    scene=scene_for(params.get('show_version','v1'))
    amplitude=params['scale']/100*2047
    rgb=tuple(round(c/255*4095*params['brightness']/100) for c in (255,105,180))
    result=[]
    for index,(x,y) in enumerate(scene['heart_centres']):
        cycle=elapsed*math.tau
        cx=x+.02*math.cos(cycle*.11+index);cy=y+.035*math.sin(cycle*.09+index*1.8)
        size=.11+.012*math.sin(cycle*.07+index);angle=.12*math.sin(cycle*.08+index)
        c,s=math.cos(angle),math.sin(angle)
        points=[(round(2048+(cx+(px*c-py*s)*size)*amplitude),round(2048+(cy+(px*s+py*c)*size)*amplitude),*rgb) for px,py in scene['heart_outline']]
        first,last=points[0],points[-1]
        result.extend([(first[0],first[1],0,0,0)]*8);result.extend(points);result.extend([(last[0],last[1],0,0,0)]*8)
    return result


def horror_frame(params,elapsed,svg=None,blank=False,fan=None,fan_elapsed=None):
    if blank or params['brightness']==0:return [(2048,2048,0,0,0)]*100
    version=params.get('show_version','v1');scene=scene_for(version)
    plan=scene_plan(params['gauge']['value'],version)
    if plan['hearts']:
        return combine_frames([heart_frame(dict(params,brightness=params['brightness']*plan['heart_opacity']),elapsed)])
    severity=plan['severity']
    hands=dict(params,shape='hands',colour='#ed1c24',density=plan['hands'],
               brightness=params['brightness']*(.35+.65*severity),
               _hand_centres=scene['hand_centres'],_hand_size=.29-.12*severity,
               _hand_visibility_floor=.35+.30*severity)
    frames=[hand_frame(hands,elapsed*(.1+.65*severity))]
    priorities=[1]
    fan_visible=False
    if plan['fans'] and fan:
        amplitude=params['scale']/100*2047
        fan_time=elapsed if fan_elapsed is None else fan_elapsed
        for index,(x,y,size) in enumerate(scene['fan_centres']):
            age=fan_time-index*.12
            if age<0:continue
            asset=dict(fan,_contour_limit=6)
            local=dict(params,colour='#ed1c24',scale=params['scale']*size,
                       brightness=params['brightness']*min(1,age/.3))
            samples=svg_frame(asset,local,age)
            if not any(any(point[2:]) for point in samples):continue
            fan_visible=True
            dx=round((x+.025*math.sin(elapsed*.5+index))*amplitude)
            dy=round((y+.035*math.cos(elapsed*.4+index))*amplitude)
            frames.append([(px+dx,py+dy,r,g,b) for px,py,r,g,b in samples])
            priorities.append(4)
    if svg and svg['frames']:
        # Hold the fully grown artwork so blood remains visible while below its threshold.
        final=svg['frames'][-1]
        amplitude=params['scale']/100*2047
        for index,instance in enumerate(blood_instances(plan,version)):
            animated=svg.get('kind')=='gif'
            asset=(dict(svg,_contour_limit=instance['contours']) if animated else
                   dict(svg,frames=[final[:instance['contours']]],duration=0,starts=[]))
            settings=dict(params,shape='svg',colour='#ed1c24',scale=params['scale']*instance['scale'],
                          brightness=params['brightness']*instance['brightness'])
            samples=svg_frame(asset,settings,elapsed+instance['phase'] if animated else 0)
            dx,dy=round(instance['x']*amplitude),round(instance['y']*amplitude)
            frames.append([(x+dx,y+dy,r,g,b) for x,y,r,g,b in samples])
            priorities.append(2 if instance['contours']==2 else 1)
    return combine_frames(frames,budget=1100 if fan_visible else 1000,priorities=priorities)
