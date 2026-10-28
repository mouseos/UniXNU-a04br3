// Metal framebuffer-fetch probe; the second render target records the fetched
// value including alpha. No source compilation or CPU rendering on device.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <cmath>
#include <cstdio>
#include <vector>

int main(int argc, char **argv) {
 @autoreleasepool {
  if (argc != 2) { fprintf(stderr,"usage: xr550-probe shader.metallib\n"); return 2; }
  id<MTLDevice> d = MTLCreateSystemDefaultDevice();
  if (!d) { fprintf(stderr,"FAIL no Metal device\n"); return 1; }
  NSError *error = nil;
  id<MTLLibrary> lib = [d newLibraryWithFile:@(argv[1]) error:&error];
  if (!lib) { fprintf(stderr,"FAIL library %s\n",error.localizedDescription.UTF8String); return 1; }
  id<MTLFunction> vertex = [lib newFunctionWithName:@"xr_vertex"];
  id<MTLFunction> writers[2] = {[lib newFunctionWithName:@"xr_write"], [lib newFunctionWithName:@"xr_fetch"]};
  if (!vertex || !writers[0] || !writers[1]) { fprintf(stderr,"FAIL functions\n"); return 1; }
  id<MTLRenderPipelineState> pipelines[2];
  for (int i=0;i<2;i++) {
   MTLRenderPipelineDescriptor *p = [MTLRenderPipelineDescriptor new];
   p.vertexFunction=vertex; p.fragmentFunction=writers[i];
   p.colorAttachments[0].pixelFormat=(MTLPixelFormat)550;
   p.colorAttachments[1].pixelFormat=MTLPixelFormatRGBA32Float;
   error=nil; pipelines[i]=[d newRenderPipelineStateWithDescriptor:p error:&error];
   if (!pipelines[i]) { fprintf(stderr,"FAIL pipeline %d %s\n",i,error.localizedDescription.UTF8String); return 1; }
  }
  const NSUInteger w=32,h=32;
  MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:(MTLPixelFormat)550 width:w height:h mipmapped:NO];
  td.usage=MTLTextureUsageRenderTarget; id<MTLTexture> xr=[d newTextureWithDescriptor:td];
  td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:w height:h mipmapped:NO];
  td.usage=MTLTextureUsageRenderTarget; id<MTLTexture> out=[d newTextureWithDescriptor:td];
  id<MTLCommandQueue> queue=[d newCommandQueue];
  if (!xr || !out || !queue) { fprintf(stderr,"FAIL resources\n"); return 1; }
  // Values cover negative, unity, above-unity and independent alpha. Broad
  // tolerances reflect 10-bit XR and 8-bit alpha quantization, not an oracle
  // for every tie-breaking or half-precision conversion.
  const float input[][4]={{0,0,0,0},{1,1,1,1},{-0.5f,0.25f,1.2f,0.5f},{0.75f,-0.25f,0.5f,0.25f}};
  for (unsigned c=0;c<sizeof(input)/sizeof(input[0]);c++) {
   MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
   pass.colorAttachments[0].texture=xr; pass.colorAttachments[1].texture=out;
   for(int i=0;i<2;i++) { pass.colorAttachments[i].loadAction=MTLLoadActionClear; pass.colorAttachments[i].storeAction=MTLStoreActionStore; }
   pass.colorAttachments[1].clearColor=MTLClearColorMake(0.37,0.37,0.37,0.37);
   id<MTLCommandBuffer> cb=[queue commandBuffer]; id<MTLRenderCommandEncoder> enc=[cb renderCommandEncoderWithDescriptor:pass];
   if (!cb || !enc) { fprintf(stderr,"FAIL encoder\n"); return 1; }
   [enc setRenderPipelineState:pipelines[0]]; [enc setFragmentBytes:input[c] length:sizeof(input[c]) atIndex:0];
   [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
   [enc setRenderPipelineState:pipelines[1]]; [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
   [enc endEncoding]; [cb commit]; [cb waitUntilCompleted];
   if (cb.status != MTLCommandBufferStatusCompleted) { fprintf(stderr,"FAIL command case=%u %s\n",c,cb.error.localizedDescription.UTF8String); return 1; }
   std::vector<float> values(w*h*4, NAN);
   [out getBytes:values.data() bytesPerRow:w*16 fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
   unsigned bad=0; for (NSUInteger i=0;i<w*h;i++) for(unsigned j=0;j<4;j++) {
    float v=values[i*4+j]; if (!std::isfinite(v) || std::fabs(v-input[c][j])>0.006f) { bad++; if(bad==1) fprintf(stderr,"FAIL case=%u pixel=%lu lane=%u got=%g expected=%g\n",c,(unsigned long)i,j,v,input[c][j]); }
   }
   if(bad) return 1;
   printf("PASS XR550 Metal fetch case=%u pixels=%lu color+alpha\n",c,(unsigned long)(w*h));
  }
  return 0;
 }
}
