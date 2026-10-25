"""Bounded SVG outlines for the scanner; XML is never executed or displayed.

Supports common geometry, transforms and the numbered CSS frame groups used by
blood1.svg. Curves are flattened then resampled within an 800-point frame budget.
"""
import bisect
import math
import re
import xml.etree.ElementTree as ET

NUMBER = r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?'
TOKENS = re.compile(NUMBER + r'|[A-Za-z]')
IDENTITY = (1, 0, 0, 1, 0, 0)
MAX_BYTES = 2 * 1024 * 1024
MAX_PATHS = 24
FRAME_POINTS = 800


def numbers(text):
    if re.sub(NUMBER, '', text).strip(' ,\t\n\r'):
        raise ValueError('SVGの数値を読み取れません')
    values = [float(value) for value in re.findall(NUMBER, text)]
    if any(not math.isfinite(value) or abs(value) > 1e7 for value in values):
        raise ValueError('SVGの座標が範囲外です')
    return values


def multiply(a, b):
    return (a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1],
            a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3],
            a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5])


def transform(text):
    result = IDENTITY
    for match in re.finditer(r'([A-Za-z]+)\s*\(([^)]*)\)', text):
        name, values = match[1], numbers(match[2])
        if name == 'matrix' and len(values) == 6:
            matrix = tuple(values)
        elif name == 'translate' and 1 <= len(values) <= 2:
            matrix = (1, 0, 0, 1, values[0], values[1] if len(values) == 2 else 0)
        elif name == 'scale' and 1 <= len(values) <= 2:
            matrix = (values[0], 0, 0, values[-1], 0, 0)
        elif name == 'rotate' and len(values) in (1, 3):
            angle = math.radians(values[0]); c, s = math.cos(angle), math.sin(angle)
            matrix = (c, s, -s, c, 0, 0)
            if len(values) == 3:
                x, y = values[1:]
                matrix = multiply(multiply((1, 0, 0, 1, x, y), matrix), (1, 0, 0, 1, -x, -y))
        elif name in ('skewX', 'skewY') and len(values) == 1:
            skew = math.tan(math.radians(values[0]))
            matrix = (1, 0, skew, 1, 0, 0) if name == 'skewX' else (1, skew, 0, 1, 0, 0)
        else:
            raise ValueError('未対応のSVG変換です: ' + name)
        result = multiply(result, matrix)
    if re.sub(r'[A-Za-z]+\s*\([^)]*\)', '', text).strip(' ,\t\n\r'):
        raise ValueError('SVGの変換を読み取れません')
    return result


def apply(matrix, point):
    a, b, c, d, e, f = matrix; x, y = point
    return (a*x+c*y+e, b*x+d*y+f)


def arc(start, values):
    # SVG endpoint-to-centre conversion: w3.org/TR/SVG/implnote.html
    rx, ry, rotation, large, sweep, x, y = values
    if large not in (0, 1) or sweep not in (0, 1):
        raise ValueError('SVGの円弧フラグが正しくありません')
    if start == (x, y):
        return []
    rx, ry = abs(rx), abs(ry)
    if not rx or not ry:
        return [(x, y)]
    phi = math.radians(rotation % 360); c, s = math.cos(phi), math.sin(phi)
    dx, dy = (start[0]-x)/2, (start[1]-y)/2
    xp, yp = c*dx+s*dy, -s*dx+c*dy
    factor = max(1, math.sqrt((xp/rx)**2+(yp/ry)**2)); rx *= factor; ry *= factor
    denominator = (rx*yp)**2+(ry*xp)**2
    coefficient = math.sqrt(max(0, ((rx*ry)**2-denominator)/max(denominator, 1e-30)))
    if large == sweep:
        coefficient = -coefficient
    cxp, cyp = coefficient*rx*yp/ry, -coefficient*ry*xp/rx
    cx, cy = c*cxp-s*cyp+(start[0]+x)/2, s*cxp+c*cyp+(start[1]+y)/2
    ux, uy, vx, vy = (xp-cxp)/rx, (yp-cyp)/ry, (-xp-cxp)/rx, (-yp-cyp)/ry
    theta = math.atan2(uy, ux); delta = math.atan2(ux*vy-uy*vx, ux*vx+uy*vy)
    if sweep and delta < 0: delta += math.tau
    if not sweep and delta > 0: delta -= math.tau
    count = max(2, math.ceil(abs(delta)/(math.pi/24)))
    result = []
    for i in range(1, count+1):
        angle = theta+delta*i/count
        result.append((cx+rx*math.cos(angle)*c-ry*math.sin(angle)*s,
                       cy+rx*math.cos(angle)*s+ry*math.sin(angle)*c))
    result[-1] = (x, y)
    return result


def path_outlines(text):
    if TOKENS.sub('', text).strip(' ,\t\n\r'):
        raise ValueError('SVGパスを読み取れません')
    tokens = TOKENS.findall(text)
    if len(tokens) > 150000:
        raise ValueError('SVGパスが複雑すぎます')
    paths, points = [], []
    current = origin = (0, 0)
    previous, control, command, i = '', None, None, 0
    counts = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'C': 6, 'S': 4, 'Q': 4, 'T': 2, 'A': 7}
    while i < len(tokens):
        if tokens[i].isalpha():
            command = tokens[i]; i += 1
            if command.upper() == 'Z':
                if points:
                    if current != origin: points.append(origin)
                    paths.append(points); points = []
                current = origin; previous = 'Z'; command = None; control = None
                continue
        if not command or command.upper() not in counts:
            raise ValueError('未対応または不正なSVGパス命令です')
        kind, relative = command.upper(), command.islower()
        count = counts[kind]
        if i+count > len(tokens) or any(value.isalpha() for value in tokens[i:i+count]):
            raise ValueError('SVGパスの座標が不足しています')
        values = [float(value) for value in tokens[i:i+count]]; i += count
        if any(not math.isfinite(v) or abs(v) > 1e7 for v in values):
            raise ValueError('SVGパスの座標が範囲外です')
        x, y = current
        if relative:
            if kind == 'H': values[0] += x
            elif kind == 'V': values[0] += y
            elif kind == 'A': values[-2] += x; values[-1] += y
            else:
                values = [v+(x if n % 2 == 0 else y) for n, v in enumerate(values)]
        if kind == 'M':
            if points: paths.append(points)
            current = origin = tuple(values); points = [current]
            command = 'l' if relative else 'L'
        else:
            if not points: points = [current]
            if kind in ('L', 'H', 'V'):
                current = tuple(values) if kind == 'L' else (values[0], y) if kind == 'H' else (x, values[0])
                points.append(current)
            elif kind in ('C', 'S', 'Q', 'T'):
                reflected = (2*x-control[0], 2*y-control[1]) if control else current
                if kind == 'C': a, b, end = tuple(values[:2]), tuple(values[2:4]), tuple(values[4:])
                elif kind == 'S': a, b, end = reflected if previous in ('C', 'S') else current, tuple(values[:2]), tuple(values[2:])
                elif kind == 'Q': a, end = tuple(values[:2]), tuple(values[2:])
                else: a, end = reflected if previous in ('Q', 'T') else current, tuple(values)
                for n in range(1, 13):
                    t = n/12; u = 1-t
                    if kind in ('C', 'S'):
                        points.append(tuple(u**3*current[k]+3*u*u*t*a[k]+3*u*t*t*b[k]+t**3*end[k] for k in (0, 1)))
                    else:
                        points.append(tuple(u*u*current[k]+2*u*t*a[k]+t*t*end[k] for k in (0, 1)))
                control = b if kind in ('C', 'S') else a
                current = end
            else:
                points.extend(arc(current, values)); current = tuple(values[-2:])
        if kind not in ('C', 'S', 'Q', 'T'): control = None
        previous = kind
    if points: paths.append(points)
    return [path for path in paths if len(path) >= 2]


def geometry(element):
    tag = element.tag.split('}')[-1]
    def value(name, default=0):
        found = numbers(element.get(name, str(default)))
        if len(found) != 1: raise ValueError('SVGの寸法には数値を指定してください')
        return found[0]
    if tag == 'path': return path_outlines(element.get('d', ''))
    if tag in ('polygon', 'polyline'):
        values = numbers(element.get('points', ''))
        if len(values) % 2: raise ValueError('SVGの頂点が不足しています')
        points = list(zip(values[::2], values[1::2]))
        if tag == 'polygon' and points: points.append(points[0])
        return [points]
    if tag == 'line': return [[(value('x1'), value('y1')), (value('x2'), value('y2'))]]
    if tag in ('circle', 'ellipse'):
        x, y = value('cx'), value('cy'); rx = value('r') if tag == 'circle' else value('rx'); ry = rx if tag == 'circle' else value('ry')
        if rx <= 0 or ry <= 0: return []
        return [[(x+rx*math.cos(i*math.tau/96), y+ry*math.sin(i*math.tau/96)) for i in range(97)]]
    if tag == 'rect':
        x, y, w, h = value('x'), value('y'), value('width'), value('height')
        if w <= 0 or h <= 0: return []
        rx = min(abs(value('rx', value('ry'))), w/2); ry = min(abs(value('ry', rx)), h/2)
        if not rx or not ry: return [[(x,y), (x+w,y), (x+w,y+h), (x,y+h), (x,y)]]
        return path_outlines('M %s %s H %s A %s %s 0 0 1 %s %s V %s A %s %s 0 0 1 %s %s H %s A %s %s 0 0 1 %s %s V %s A %s %s 0 0 1 %s %s Z' %
                             (x+rx,y,x+w-rx,rx,ry,x+w,y+ry,y+h-ry,rx,ry,x+w-rx,y+h,x+rx,rx,ry,x,y+h-ry,y+ry,rx,ry,x+rx,y))
    return []


def pale(paint):
    paint = paint.strip().lower()
    if paint in ('white', 'transparent'): return True
    if re.fullmatch(r'#[0-9a-f]{3}', paint): paint = '#'+''.join(c*2 for c in paint[1:])
    if re.fullmatch(r'#[0-9a-f]{6}', paint): return min(int(paint[i:i+2], 16) for i in (1, 3, 5)) >= 205
    if paint.startswith('rgb('):
        values = numbers(paint[4:-1])
        return len(values) == 3 and min(values) >= 205
    return False


def resample(points, count):
    distances = [0.0]
    for a, b in zip(points, points[1:]): distances.append(distances[-1]+math.hypot(b[0]-a[0], b[1]-a[1]))
    result = []
    for i in range(count):
        distance = distances[-1]*i/(count-1)
        j = min(max(1, bisect.bisect_left(distances, distance)), len(points)-1)
        ratio = (distance-distances[j-1])/max(distances[j]-distances[j-1], 1e-12)
        result.append([round(points[j-1][k]+(points[j][k]-points[j-1][k])*ratio, 6) for k in (0, 1)])
    return result


def import_svg(source, name='drawing.svg', ignore_pale=True):
    if not isinstance(source, str) or len(source.encode('utf-8')) > MAX_BYTES:
        raise ValueError('SVGは2MB以内のファイルを選んでください')
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', source, re.I):
        raise ValueError('外部定義を含むSVGは読み込めません')
    try: root = ET.fromstring(source)
    except ET.ParseError: raise ValueError('SVGのXMLを読み取れません')
    if root.tag.split('}')[-1] != 'svg': raise ValueError('SVGファイルを選んでください')
    if sum(1 for _ in root.iter()) > 10000: raise ValueError('SVGの要素が多すぎます')
    if not isinstance(ignore_pale, bool): raise ValueError('背景除外はON/OFFで指定してください')
    css = '\n'.join(e.text or '' for e in root.iter() if e.tag.split('}')[-1] == 'style')
    groups = [(int(m[1]), e) for e in root for m in [re.search(r'(?:^|\s)f(\d+)(?:\s|$)', e.get('class', ''))] if m]
    groups.sort(key=lambda item: item[0])
    if len(groups) > 120: raise ValueError('SVGアニメーションは120コマ以内にしてください')
    unsupported, raw_frames, dropped, processed = set(), [], 0, [0]
    supported = {'path', 'rect', 'circle', 'ellipse', 'polygon', 'polyline', 'line'}
    def walk(element, matrix=IDENTITY, inherited=None, depth=0):
        if depth > 40: raise ValueError('SVGの階層が深すぎます')
        tag = element.tag.split('}')[-1]
        if tag in ('defs', 'style', 'title', 'desc', 'metadata'): return []
        if tag not in supported | {'g', 'svg'}:
            unsupported.add(tag); return []
        style = dict(inherited or {'fill': 'black', 'stroke': 'none'})
        for key in ('fill', 'stroke', 'display', 'visibility', 'opacity', 'fill-opacity', 'stroke-opacity'):
            if key in element.attrib: style[key] = element.get(key)
        style.update(dict(re.findall(r'([\w-]+)\s*:\s*([^;]+)', element.get('style', ''))))
        if style.get('display') == 'none' or style.get('visibility') == 'hidden' or style.get('opacity') == '0': return []
        matrix = multiply(matrix, transform(element.get('transform', '')))
        if tag in ('g', 'svg'):
            return [path for child in element for path in walk(child, matrix, style, depth+1)]
        paint = style['stroke'] if style['stroke'] != 'none' else style['fill']
        if paint == 'none' or (ignore_pale and pale(paint)): return []
        paths = [[apply(matrix, point) for point in path] for path in geometry(element)]
        processed[0] += sum(len(path) for path in paths)
        if processed[0] > 400000: raise ValueError('SVGが複雑すぎます。輪郭を簡略化してください')
        return paths
    root_matrix = transform(root.get('transform', ''))
    inherited = {key: root.get(key, default) for key, default in (('fill', 'black'), ('stroke', 'none'))}
    for _, element in groups or [(0, root)]:
        raw_frames.append(walk(element, root_matrix, inherited) if groups else walk(element))
    all_points = [point for frame in raw_frames for path in frame for point in path]
    if not all_points: raise ValueError('描ける輪郭がありません。文字はパスに変換し、背景除外の設定も確認してください')
    if any(not math.isfinite(v) or abs(v) > 1e12 for point in all_points for v in point): raise ValueError('SVGの変換後座標が範囲外です')
    low_x = min(p[0] for p in all_points); high_x = max(p[0] for p in all_points)
    low_y = min(p[1] for p in all_points); high_y = max(p[1] for p in all_points)
    span = max(high_x-low_x, high_y-low_y)
    if span <= 1e-8: raise ValueError('点だけのSVGは投影できません')
    centre = ((low_x+high_x)/2, (low_y+high_y)/2)
    frames = []
    for frame in raw_frames:
        measured = []
        for path in frame:
            points = [((p[0]-centre[0])*1.8/span, (p[1]-centre[1])*1.8/span) for p in path]
            length = sum(math.hypot(b[0]-a[0], b[1]-a[1]) for a, b in zip(points, points[1:]))
            if length >= .012: measured.append((length, points))
            else: dropped += 1
        measured.sort(key=lambda item: item[0], reverse=True)
        dropped += max(0, len(measured)-MAX_PATHS); measured = measured[:MAX_PATHS]
        total = sum(length for length, _ in measured)
        available = FRAME_POINTS-len(measured)*16
        extra = available-len(measured)*8
        frames.append([resample(points, 8+int(extra*length/total)) for length, points in measured] if total else [])
    if not any(frames): raise ValueError('描ける長さの輪郭がありません')
    duration = 0.0; starts = []
    if groups:
        period = re.search(r'animation\s*:\s*f\d+\s+('+NUMBER+r')(ms|s)\b', css)
        duration = float(period[1])/(1000 if period[2] == 'ms' else 1) if period else len(groups)/15
        duration = min(120, max(.5, duration))
        for index, _ in groups:
            match = re.search(r'@keyframes\s+f'+str(index)+r'\s*\{(.*?)(?=\n@keyframes|\Z)', css, re.S)
            visible = re.search('('+NUMBER+r')%\s*\{\s*visibility\s*:\s*visible', match[1]) if match else None
            starts.append(float(visible[1])/100 if visible else len(starts)/len(groups))
        if any(not 0 <= value < 1 for value in starts) or starts != sorted(starts): starts = [i/len(groups) for i in range(len(groups))]
    warnings = ['塗り・陰影は輪郭線に変換します。色は画面の色設定を使います。']
    if ignore_pale: warnings.append('白・淡色の背景や輪郭を除外しました。')
    if dropped: warnings.append('細部を省略しました（各コマ最大24輪郭・800点）。')
    if unsupported: warnings.append('未対応の要素を省略: '+', '.join(sorted(unsupported)))
    if 'animation' in css and not groups: warnings.append('この形式のCSSアニメーションは静止画として読み込みました。')
    return {'name': str(name).replace('\\', '/').split('/')[-1][:120], 'frames': frames,
            'duration': duration, 'starts': starts, 'warnings': warnings,
            'max_paths': max(map(len, frames)), 'max_points': max(sum(len(p)+16 for p in f) for f in frames)}


def svg_frame(asset, params, elapsed=0.0, blank=False):
    frames = asset['frames']
    if asset['duration']:
        fraction = (elapsed/asset['duration']) % 1
        index = max(0, bisect.bisect_right(asset['starts'], fraction)-1)
    else: index = 0
    paths = frames[index]
    if asset.get('_contour_limit'):
        paths = paths[:asset['_contour_limit']]
    if not paths: return [(2048, 2048, 0, 0, 0)]*100
    scale = params['scale']/100*2047
    angle = elapsed*math.tau
    # Animated artwork plays in place; its speed control changes playback speed.
    if asset['duration']: angle = 0
    c, s = math.cos(angle), math.sin(angle)
    colour = params['colour'].lstrip('#')
    opacity = asset['frame_opacity'][index] if asset.get('frame_opacity') else 1
    rgb = tuple(round(int(colour[i:i+2], 16)/255*4095*params['brightness']/100*opacity) for i in (0, 2, 4)) if not blank else (0, 0, 0)
    # Keep screen coordinates here; the common UDP encoder converts scanner Y.
    transformed = [[(round(2048+(x*c-y*s)*scale), round(2048+(x*s+y*c)*scale)) for x,y in path] for path in paths]
    result = []; previous = transformed[-1][-1]
    for path_index,path in enumerate(transformed):
        if asset.get('frame_colours'):
            colour=asset['frame_colours'][index][path_index].lstrip('#')
            rgb=tuple(round(int(colour[i:i+2],16)/255*4095*params['brightness']/100*opacity) for i in (0,2,4)) if not blank else (0,0,0)
        first = path[0]
        result.extend((round(previous[0]+(first[0]-previous[0])*i/8), round(previous[1]+(first[1]-previous[1])*i/8), 0, 0, 0) for i in range(1, 9))
        result.extend([(*first, 0, 0, 0)]*4)
        result.extend((*point, *rgb) for point in path)
        previous = path[-1]; result.extend([(*previous, 0, 0, 0)]*4)
    return result
