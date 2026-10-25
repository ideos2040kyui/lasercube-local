"""Two independently controlled vector layers, composed into one scanner frame."""
import bisect
import math
import re

FRAME_POINTS = 1000
SOURCES = ('circle', 'eight', 'triangle', 'hands', 'svg')


def default_layers():
    base = {'enabled': True, 'brightness': 100.0, 'scale': 100.0,
            'colour': '#ed1c24', 'x': 0.0, 'y': 0.0, 'density': 3}
    return [dict(base, shape='hands', speed=.15), dict(base, shape='svg', speed=1.0)]


def validate_layers(value, previous=None):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError('レイヤーはA・Bの2つを指定してください')
    result = []
    for index, data in enumerate(value):
        if not isinstance(data, dict):
            raise ValueError('レイヤー設定が正しくありません')
        layer = dict((previous or default_layers())[index])
        if 'enabled' in data:
            if not isinstance(data['enabled'], bool): raise ValueError('レイヤーの表示はON/OFFで指定してください')
            layer['enabled'] = data['enabled']
        if 'shape' in data:
            if data['shape'] not in SOURCES: raise ValueError('未対応のレイヤー図形です')
            layer['shape'] = data['shape']
        if 'colour' in data:
            if not isinstance(data['colour'], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', data['colour']):
                raise ValueError('レイヤーの色が正しくありません')
            layer['colour'] = data['colour']
        for key, low, high in (('brightness', 0, 100), ('scale', 10, 100), ('speed', 0, 1), ('x', -100, 100), ('y', -100, 100), ('density', 1, 8)):
            if key not in data: continue
            v = data[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high:
                raise ValueError('レイヤーの'+key+'が範囲外です')
            if key == 'density' and int(v) != v: raise ValueError('手の数は1〜8の整数を指定してください')
            layer[key] = int(v) if key == 'density' else float(v)
        result.append(layer)
    return result


def resample(points, count):
    distances = [0.0]
    for a, b in zip(points, points[1:]):
        distances.append(distances[-1]+math.hypot(b[0]-a[0], b[1]-a[1]))
    result = []
    for i in range(count):
        distance = distances[-1]*i/(count-1)
        j = min(max(1, bisect.bisect_left(distances, distance)), len(points)-1)
        ratio = (distance-distances[j-1])/max(distances[j]-distances[j-1], 1e-12)
        result.append(tuple(round(points[j-1][k]+(points[j][k]-points[j-1][k])*ratio) for k in range(5)))
    return result


def compose_frame(params, elapsed, render, svg=None, layer_svgs=None, blank=False):
    if blank or params['brightness'] == 0:
        return [(2048, 2048, 0, 0, 0)]*100
    frames = []
    for index, layer in enumerate(params.get('layers', default_layers())):
        if not layer['enabled'] or layer['brightness'] == 0: continue
        asset = ((layer_svgs or [None, None])[index] or svg)
        if layer['shape'] == 'svg' and asset is None: continue
        child = dict(params, shape=layer['shape'], colour=layer['colour'], density=layer['density'],
                     brightness=params['brightness']*layer['brightness']/100,
                     scale=params['scale']*layer['scale']/100)
        samples = render(child, elapsed*layer['speed'], svg=asset)
        amplitude = params['scale']/100*2047
        dx, dy = round(amplitude*layer['x']/100), round(amplitude*layer['y']/100)
        frames.append([(x+dx, y+dy, red, green, blue) for x,y,red,green,blue in samples])
    return combine_frames(frames)


def combine_frames(frames, budget=FRAME_POINTS, priorities=None):
    strokes = []
    importance = []
    for frame_index,samples in enumerate(frames):
        priority = priorities[frame_index] if priorities else 1
        current = []
        for point in samples:
            if any(point[2:]): current.append(point)
            elif current:
                if len(current) >= 2:
                    strokes.append(current);importance.append(priority)
                current = []
        if len(current) >= 2:
            strokes.append(current);importance.append(priority)
    if not strokes: return [(2048, 2048, 0, 0, 0)]*100
    # At most 48 contours (two SVGs). Preserve all pen-up boundaries when reducing detail.
    total_points = sum(len(path)+16 for path in strokes)
    if total_points > budget:
        available = budget-len(strokes)*20  # 16 blank + 4 lit minimum per contour.
        weights = [priority*sum(math.hypot(b[0]-a[0], b[1]-a[1]) for a,b in zip(p,p[1:])) for p,priority in zip(strokes,importance)]
        weight = sum(weights)
        strokes = [resample(path, 4+int(available*length/max(weight, 1e-12))) for path,length in zip(strokes,weights)]
    result = []; previous = strokes[-1][-1]
    for path in strokes:
        first = path[0]
        result.extend((round(previous[0]+(first[0]-previous[0])*i/8), round(previous[1]+(first[1]-previous[1])*i/8), 0, 0, 0) for i in range(1,9))
        result.extend([(first[0], first[1], 0, 0, 0)]*4)
        result.extend(path)
        previous = path[-1]; result.extend([(previous[0], previous[1], 0, 0, 0)]*4)
    return result
