"""Code-native hand outlines shared with the canvas preview."""
import json
import math
from pathlib import Path

HAND = json.loads((Path(__file__).parent / 'static' / 'hand.json').read_text())


def smooth_outline(points):
    points = [tuple(point) for point in points]
    for _ in range(2):
        smoothed = []
        for index, point in enumerate(points):
            following = points[(index + 1) % len(points)]
            smoothed.extend(((point[0] * .75 + following[0] * .25, point[1] * .75 + following[1] * .25),
                             (point[0] * .25 + following[0] * .75, point[1] * .25 + following[1] * .75)))
        points = smoothed
    return points


def resample_closed(points, count=84):
    lengths = [math.hypot(points[(i + 1) % len(points)][0] - point[0],
                          points[(i + 1) % len(points)][1] - point[1]) for i, point in enumerate(points)]
    perimeter = sum(lengths)
    result, edge, traversed = [], 0, 0.0
    for index in range(count):
        distance = perimeter * index / (count - 1)
        while edge < len(points) - 1 and traversed + lengths[edge] < distance:
            traversed += lengths[edge]
            edge += 1
        amount = (distance - traversed) / max(lengths[edge], 1e-12)
        a, b = points[edge], points[(edge + 1) % len(points)]
        result.append(((a[0] + (b[0] - a[0]) * amount) / 1.35,
                       (a[1] + (b[1] - a[1]) * amount) / 1.35))
    return result


OUTLINE = resample_closed(smooth_outline(HAND['outline']))


def hand_frame(params, phase, blank=False):
    count = int(params.get('density', 4))
    colour = params['colour'].lstrip('#')
    rgb = tuple(round(int(colour[i:i+2], 16) / 255 * 4095 * params['brightness'] / 100) for i in (0, 2, 4))
    if blank:
        rgb = (0, 0, 0)
    amplitude = params['scale'] / 100 * 2047
    result = []
    for index in range(count):
        base_x, base_y = HAND['centres'][index]
        cycle = phase * math.tau
        centre_x = base_x + .045 * math.sin(cycle * .73 + index * 1.7)
        centre_y = base_y + .045 * math.cos(cycle * .57 + index * 2.1)
        angle = -.20 + .20 * math.sin(cycle * .43 + index * 1.3)
        size = .27 + .025 * math.sin(cycle * .61 + index * .9)
        cosine, sine = math.cos(angle), math.sin(angle)
        mirror = -1 if index % 2 else 1
        visibility = .15 + .85 * ((1 + math.sin(cycle * .23 + index * 1.9)) / 2) ** 1.6
        hand_rgb = tuple(round(value * visibility) for value in rgb)
        points = []
        for x, y in OUTLINE:
            x *= mirror
            px = centre_x + (x * cosine - y * sine) * size
            py = centre_y + (x * sine + y * cosine) * size
            points.append((round(2048 + px * amplitude), round(2048 + py * amplitude), *hand_rgb))
        first, last = points[0], points[-1]
        result.extend([(first[0], first[1], 0, 0, 0)] * 8)
        result.extend(points)
        result.extend([(last[0], last[1], 0, 0, 0)] * 8)
    return result
