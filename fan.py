"""Emphasize a readable eye within the GIF fan outline for laser projection."""
import hashlib
import math
from svg_import import resample


def prepare_fan_asset(asset):
    if asset.get('fan_eye_emphasis'):
        return asset
    bodies=[frame[0] if frame else [] for frame in asset['frames']]
    max_width=max((max(p[0] for p in body)-min(p[0] for p in body) for body in bodies if body),default=1)
    frames=[]
    for body in bodies:
        if not body:frames.append([]);continue
        left,right=min(p[0] for p in body),max(p[0] for p in body)
        top,bottom=min(p[1] for p in body),max(p[1] for p in body)
        width=right-left
        paths=[resample(body,min(160,len(body))) if len(body)>160 else body]
        if width/max(max_width,1e-9)>.35:
            cx,cy=(left+right)/2,top+(bottom-top)*.43
            half_width=width*.26;eye_height=half_width*.38
            eye=[]
            for side in (-1,1):
                start=(-half_width,0) if side==-1 else (half_width,0)
                end=(half_width,0) if side==-1 else (-half_width,0)
                a=(start[0]*.42,side*eye_height);b=(end[0]*.42,side*eye_height)
                for i in range(13):
                    t=i/12;u=1-t
                    eye.append([cx+u**3*start[0]+3*u*u*t*a[0]+3*u*t*t*b[0]+t**3*end[0],
                                cy+3*u*u*t*a[1]+3*u*t*t*b[1]])
            pupil=[[cx+half_width*.19*math.cos(i*math.tau/20),cy+half_width*.19*math.sin(i*math.tau/20)] for i in range(21)]
            pupil[-1]=pupil[0][:]
            paths.extend((eye,pupil))
        frames.append(paths)
    return dict(asset,frames=frames,fan_eye_emphasis=True,max_paths=max(map(len,frames)),
                max_points=max(sum(len(path)+16 for path in frame) for frame in frames),
                revision=hashlib.sha256((asset['revision']+':large-eye-v1').encode()).hexdigest()[:16],
                warnings=asset.get('warnings',[])+['レーザーで目が分かるよう、骨組みを省略して目と瞳を強調しています。'])
