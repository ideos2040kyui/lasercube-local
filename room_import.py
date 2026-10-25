"""Trace the room silhouette and large red/blue regions, retaining GIF timing."""
import hashlib
import json
import math
from pathlib import Path

from gif_import import trace_mask
from svg_import import resample


def area(path):
    return abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(path,path[1:])))/2


def convert_room_decoded(directory,name):
    directory=Path(directory);meta=json.loads((directory/'metadata.json').read_text())
    width,height,count=meta['width'],meta['height'],meta['frames'];stride=width*height*4
    data=(directory/'frames.rgba').read_bytes()
    if len(data)!=stride*count:raise ValueError('GIFのデコードデータが不足しています')
    frames=[];colours=[];starts=[];time=0;duration=sum(meta['delays'])
    for index in range(count):
        rgba=data[index*stride:(index+1)*stride]
        masks=[bytearray(width*height) for _ in range(3)]
        for i in range(width*height):
            r,g,b=rgba[i*4:i*4+3]
            masks[0][i]=min(r,g,b)<135
            masks[1][i]=r>80 and r>g*1.4 and r>b*1.25
            masks[2][i]=b>60 and b>r*1.3 and b>g*1.2
        outlines=sorted(trace_mask(masks[0],width,height),key=area,reverse=True)
        largest=area(outlines[0]) if outlines else 0
        # Tiny isolated lettering/shadows are not part of the room geometry.
        paths=[(outlines[0],'#d9dded')] if largest else []
        for mask,colour in zip(masks[1:],('#ed1c24','#245bff')):
            regions=sorted(trace_mask(mask,width,height),key=area,reverse=True)
            paths.extend((path,colour) for path in regions[:4] if area(path)>largest*.015)
        measured=[]
        for path,colour in paths:
            length=sum(math.hypot(b[0]-a[0],b[1]-a[1]) for a,b in zip(path,path[1:]))
            if length:measured.append((length,path,colour))
        total=sum(item[0] for item in measured);extra=640-len(measured)*24
        frames.append([resample(path,8+int(extra*length/total)) for length,path,_ in measured] if total else [])
        colours.append([colour for _,_,colour in measured]);starts.append(time/duration);time+=meta['delays'][index]
    points=[p for frame in frames for path in frame for p in path]
    if points:
        low=[min(p[i] for p in points) for i in (0,1)];high=[max(p[i] for p in points) for i in (0,1)]
        centre=[(a+b)/2 for a,b in zip(low,high)];span=max(b-a for a,b in zip(low,high))
        frames=[[[[round((p[i]-centre[i])*1.8/span,6) for i in (0,1)] for p in path] for path in frame] for frame in frames]
    asset={'name':name,'kind':'gif','frames':frames,'frame_colours':colours,'starts':starts,'duration':duration,
           'frame_opacity':[1]*count,'max_paths':max(map(len,frames)),
           'max_points':max(sum(len(path)+16 for path in frame) for frame in frames),
           'warnings':['部屋の外形と赤・青の面の境界を輪郭へ変換しました。塗り面と細いひびは簡略化しています。']}
    asset['revision']=hashlib.sha256(json.dumps(asset,sort_keys=True).encode()).hexdigest()[:16]
    return asset
