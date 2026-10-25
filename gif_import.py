"""Read animated GIFs through macOS ImageIO, then trace bounded laser outlines."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import tempfile
from svg_import import resample

ROOT=Path(__file__).resolve().parent


def simplify(points,epsilon=.8):
    if len(points)<4:return points
    # Split a closed outline so both sides keep their end points.
    if points[0]==points[-1]:
        pivot=max(range(1,len(points)-1),key=lambda i:(points[i][0]-points[0][0])**2+(points[i][1]-points[0][1])**2)
        return simplify(points[:pivot+1],epsilon)[:-1]+simplify(points[pivot:],epsilon)
    keep={0,len(points)-1};stack=[(0,len(points)-1)]
    while stack:
        first,last=stack.pop();a,b=points[first],points[last];dx,dy=b[0]-a[0],b[1]-a[1];den=dx*dx+dy*dy
        best,index=0,None
        for i in range(first+1,last):
            p=points[i];t=max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/max(den,1e-12)))
            distance=(p[0]-a[0]-t*dx)**2+(p[1]-a[1]-t*dy)**2
            if distance>best:best,index=distance,i
        if index is not None and best>epsilon*epsilon:
            keep.add(index);stack.extend(((first,index),(index,last)))
    return [points[i] for i in sorted(keep)]


def trace_mask(mask,width,height):
    edges={}
    def add(a,b):edges.setdefault(a,[]).append(b)
    for y in range(height):
        for x in range(width):
            i=y*width+x
            if not mask[i]:continue
            if y==0 or not mask[i-width]:add((x,y),(x+1,y))
            if x==width-1 or not mask[i+1]:add((x+1,y),(x+1,y+1))
            if y==height-1 or not mask[i+width]:add((x+1,y+1),(x,y+1))
            if x==0 or not mask[i-1]:add((x,y+1),(x,y))
    paths=[]
    while edges:
        start=next(iter(edges));current=start;path=[start]
        while current in edges:
            following=edges[current].pop()
            if not edges[current]:del edges[current]
            path.append(following);current=following
            if current==start:break
        if len(path)>=13:paths.append(simplify(path))
    return paths


def convert_decoded(directory,name):
    directory=Path(directory);meta=json.loads((directory/'metadata.json').read_text())
    width,height,count=meta['width'],meta['height'],meta['frames'];stride=width*height*4
    data=(directory/'frames.rgba').read_bytes()
    if len(data)!=stride*count:raise ValueError('GIFのデコードデータが不足しています')
    frames=[];peaks=[];dur=sum(meta['delays']);starts=[];time=0.0
    for frame_index in range(count):
        rgba=data[frame_index*stride:(frame_index+1)*stride]
        corners=[0,(width-1)*4,(height-1)*width*4,(width*height-1)*4]
        background=[statistics.median(rgba[i+c] for i in corners) for c in range(3)]
        contrast=bytearray(width*height)
        for i in range(width*height):
            base=i*4
            contrast[i]=round(max(abs(rgba[base+c]-background[c]) for c in range(3)))
        peak=max(contrast);peaks.append(peak)
        threshold=max(10,peak*.22)
        mask=bytearray(v>=threshold for v in contrast)
        paths=trace_mask(mask,width,height)
        paths.sort(key=lambda p:sum(math.hypot(b[0]-a[0],b[1]-a[1]) for a,b in zip(p,p[1:])),reverse=True)
        # Include a few smaller features as well as the dominant contour/ribs.
        if len(paths)>16:
            detail=paths[8:];chosen=[detail[min(len(detail)-1,int(i*len(detail)/8))] for i in range(8)]
            paths=paths[:8]+chosen
        measured=[]
        for path in paths:
            pts=[((x-width/2)*1.8/max(width,height),(y-height/2)*1.8/max(width,height)) for x,y in path]
            length=sum(math.hypot(b[0]-a[0],b[1]-a[1]) for a,b in zip(pts,pts[1:]))
            if length>.015:measured.append((length,pts))
        total=sum(length for length,_ in measured)
        extra=800-len(measured)*24
        frames.append([resample(points,8+int(extra*length/total)) for length,points in measured] if total else [])
        starts.append(time/dur);time+=meta['delays'][frame_index]
    reference=max(peaks) or 1
    all_points=[p for frame in frames for path in frame for p in path]
    if all_points:
        low=[min(p[i] for p in all_points) for i in (0,1)]
        high=[max(p[i] for p in all_points) for i in (0,1)]
        centre=[(a+b)/2 for a,b in zip(low,high)];span=max(b-a for a,b in zip(low,high))
        if span>1e-9:
            frames=[[[[round((p[i]-centre[i])*1.8/span,6) for i in (0,1)] for p in path] for path in frame] for frame in frames]
    asset={'name':name,'frames':frames,'starts':starts,'duration':dur,'frame_opacity':[round(min(1,p/reference),4) if p>=10 else 0 for p in peaks],
           'kind':'gif','max_paths':max(map(len,frames)),'max_points':max(sum(len(p)+16 for p in f) for f in frames),
           'warnings':['GIFの全コマのタイミングを維持し、背景を除いて輪郭へ変換しました。細部は走査点数に合わせて簡略化します。']}
    asset['revision']=hashlib.sha256(json.dumps(asset,sort_keys=True).encode()).hexdigest()[:16]
    return asset


def import_gif(source,role):
    source=Path(source).resolve()
    if role not in ('blood','fan','room'):raise ValueError('Unknown GIF role')
    if source.stat().st_size>12*1024*1024:raise ValueError('GIFは12MB以内にしてください')
    if source.read_bytes()[:6] not in (b'GIF87a',b'GIF89a'):raise ValueError('GIFファイルを選んでください')
    runtime=ROOT/'.runtime';runtime.mkdir(exist_ok=True)
    binary=runtime/'gif-extract';swift=ROOT/'gif_extract.swift'
    if not binary.exists() or binary.stat().st_mtime<swift.stat().st_mtime:
        subprocess.run(['/usr/bin/swiftc',str(swift),'-O','-o',str(binary)],check=True,capture_output=True,timeout=120)
    with tempfile.TemporaryDirectory(prefix='gif-',dir=runtime) as directory:
        subprocess.run([str(binary),str(source),directory],check=True,capture_output=True,timeout=60)
        if role=='room':
            from room_import import convert_room_decoded
            asset=convert_room_decoded(directory,source.name)
        else:asset=convert_decoded(directory,source.name)
    output=runtime/('effect-%s.json'%role)
    temporary=output.with_suffix('.tmp');temporary.write_text(json.dumps(asset,separators=(',',':')));temporary.replace(output)
    return asset


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('blood');parser.add_argument('fan');parser.add_argument('--room');args=parser.parse_args()
    for role,path in (('blood',args.blood),('fan',args.fan),('room',args.room)):
        if not path:continue
        asset=import_gif(path,role)
        print('%s: %s / %d frames / %.2f seconds'%(role,asset['name'],len(asset['frames']),asset['duration']),flush=True)
