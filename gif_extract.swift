import Foundation
import ImageIO
import CoreGraphics

if CommandLine.arguments.count != 3 { fputs("usage: gif-extract input.gif output-directory\n", stderr); exit(2) }
let input = URL(fileURLWithPath:CommandLine.arguments[1]), out = URL(fileURLWithPath:CommandLine.arguments[2])
guard let source = CGImageSourceCreateWithURL(input as CFURL,nil) else { fputs("Cannot decode GIF\n",stderr);exit(1) }
let count=CGImageSourceGetCount(source)
guard count>0 && count<=400 else { fputs("GIF must have 1-400 frames\n",stderr);exit(1) }
var delays:[Double]=[]
for i in 0..<count {
 let properties=CGImageSourceCopyPropertiesAtIndex(source,i,nil) as? [CFString:Any] ?? [:]
 let gif=properties[kCGImagePropertyGIFDictionary] as? [CFString:Any] ?? [:]
 let delay=(gif[kCGImagePropertyGIFUnclampedDelayTime] as? Double) ?? (gif[kCGImagePropertyGIFDelayTime] as? Double) ?? 0.067
 delays.append(max(0.02,delay))
}
guard let first=CGImageSourceCreateImageAtIndex(source,0,nil) else {exit(1)}
let w=first.width,h=first.height
guard w>0 && h>0 && w<=800 && h<=800 && w*h*count<=40_000_000 else {fputs("GIF resolution or frame count too large\n",stderr);exit(1)}
try FileManager.default.createDirectory(at:out,withIntermediateDirectories:true)
FileManager.default.createFile(atPath:out.appendingPathComponent("frames.rgba").path,contents:nil)
let file=try FileHandle(forWritingTo:out.appendingPathComponent("frames.rgba"))
for i in 0..<count {
 guard let image=CGImageSourceCreateImageAtIndex(source,i,nil) else {exit(1)}
 guard image.width==w && image.height==h else {fputs("GIF decoder returned a partial frame\n",stderr);exit(1)}
 var pixels=[UInt8](repeating:0,count:w*h*4)
 pixels.withUnsafeMutableBytes { raw in
  let flags=CGBitmapInfo.byteOrder32Big.rawValue|CGImageAlphaInfo.premultipliedLast.rawValue
  guard let context=CGContext(data:raw.baseAddress,width:w,height:h,bitsPerComponent:8,bytesPerRow:w*4,space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:flags) else {exit(1)}
  context.setFillColor(red:1,green:1,blue:1,alpha:1);context.fill(CGRect(x:0,y:0,width:w,height:h))
  context.draw(image,in:CGRect(x:0,y:0,width:w,height:h))
  if i==count/2, let preview=context.makeImage(),let destination=CGImageDestinationCreateWithURL(out.appendingPathComponent("preview.png") as CFURL,"public.png" as CFString,1,nil) {
   CGImageDestinationAddImage(destination,preview,nil);CGImageDestinationFinalize(destination)
  }
 }
 try file.write(contentsOf:Data(pixels))
}
try file.close()
let metadata:[String:Any]=["width":w,"height":h,"frames":count,"delays":delays,"duration":delays.reduce(0,+)]
let data=try JSONSerialization.data(withJSONObject:metadata,options:[.sortedKeys])
try data.write(to:out.appendingPathComponent("metadata.json"));FileHandle.standardOutput.write(data)
