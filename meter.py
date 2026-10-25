"""Independent upper-right battery-style excitement meter."""
import json
import math
from pathlib import Path
from layers import combine_frames
from horror import SCENE

GAUGE = json.loads((Path(__file__).parent/'static'/'gauge.json').read_text())


def default_gauge():
    return {'enabled': False, 'value': 50.0, 'brightness': 100.0, 'reactive': False}


def validate_gauge(data, previous):
    if not isinstance(data, dict): raise ValueError('メーター設定が正しくありません')
    result = dict(previous)
    for key in ('enabled', 'reactive'):
        if key in data:
            if not isinstance(data[key], bool): raise ValueError('メーターの表示・連動はON/OFFで指定してください')
            result[key] = data[key]
    for key in ('value', 'brightness'):
        if key not in data: continue
        v = data[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 100:
            raise ValueError('メーターの値・明るさは0〜100を指定してください')
        result[key] = float(v)
    return result


def shorten_path(points, fraction):
    """Reveal a prefix of one fixed path; never scale or redistribute its bends."""
    if fraction <= 0: return []
    if fraction >= 1: return list(points)
    lengths = [math.hypot(b[0]-a[0], b[1]-a[1]) for a,b in zip(points,points[1:])]
    remaining = sum(lengths)*fraction
    result = [points[0]]
    for a,b,length in zip(points,points[1:],lengths):
        if remaining >= length:
            result.append(b); remaining -= length
        else:
            if remaining > 1e-9:
                t = remaining/max(length,1e-12)
                result.append((a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t))
            break
    return result


def gauge_paths(value):
    cx, cy = GAUGE['centre']; sx, sy = GAUGE['size']
    scale = lambda path: [(cx+x*sx,cy+y*sy) for x,y in path]
    paths = [scale(GAUGE['outline'])]
    if value > 0:
        paths.append(shorten_path(scale(GAUGE['fill_path']),value/100))
    return [[(round(2048+x), round(2048+y)) for x,y in path] for path in paths]


def meter_frame(params):
    gauge = params.get('gauge', default_gauge())
    strength = params['brightness']*gauge['brightness']/100
    if not gauge['enabled'] or strength == 0: return []
    pink = gauge.get('reactive',False) and gauge['value']>=SCENE['horror_at']
    colour = (255,105,180) if pink else (255,0,0)
    rgb = tuple(round(c/255*4095*strength/100) for c in colour)
    result = []
    for path in gauge_paths(gauge['value']):
        first = path[0];result.extend([(*first,0,0,0)]*8)
        for a,b in zip(path,path[1:]):
            steps=max(1,math.ceil(math.hypot(b[0]-a[0],b[1]-a[1])/12))
            for i in range(steps):
                t=i/steps;result.append((round(a[0]+(b[0]-a[0])*t),round(a[1]+(b[1]-a[1])*t),*rgb))
        result.append((*path[-1],*rgb));result.extend([(*path[-1],0,0,0)]*8)
    return result


def overlay_meter(samples, params):
    overlay = meter_frame(params)
    return combine_frames([samples,overlay],budget=1200) if overlay else samples
