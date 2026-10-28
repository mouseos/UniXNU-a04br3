// Metal framebuffer-fetch probe; the second render target records the fetched
// value including alpha. No source compilation or CPU rendering on device.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <cmath>
#include <cstdio>
#include <vector>
#include <dispatch/dispatch.h>
#include <unistd.h>
#include <fcntl.h>
#include "xr550_reference.h"

int main(int argc, char **argv) {
 @autoreleasepool {
  if (argc > 2) { fprintf(stderr,"usage: xr550-probe [shader.metallib]\n"); return 2; }
  int console=open("/dev/console",O_WRONLY); if(console>=0) {dup2(console,STDOUT_FILENO);dup2(console,STDERR_FILENO);close(console);}
  setvbuf(stdout,nullptr,_IONBF,0);
  const char *libpath=argc==2 ? argv[1] : "/private/var/ngpu/bin/xr550.metallib";
  id<MTLDevice> d = nil;
  for(unsigned attempt=0;attempt<60 && !d;attempt++) {d=MTLCreateSystemDefaultDevice();if(!d)sleep(1);}
  if (!d) { fprintf(stderr,"FAIL no Metal device\n"); return 1; }
  NSError *error = nil;
  id<MTLLibrary> lib = [d newLibraryWithFile:@(libpath) error:&error];
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
  for (unsigned c=0;c<2;c++) {
   id<MTLCommandBuffer> cb=[queue commandBuffer];
   if (!cb) { fprintf(stderr,"FAIL command buffer\n"); return 1; }
   MTLRenderPassDescriptor *write=[MTLRenderPassDescriptor renderPassDescriptor];
   write.colorAttachments[0].texture=xr; write.colorAttachments[1].texture=out;
   for (int i=0;i<2;i++) {write.colorAttachments[i].loadAction=MTLLoadActionClear;write.colorAttachments[i].storeAction=MTLStoreActionStore;}
   write.colorAttachments[1].clearColor=MTLClearColorMake(0.37,0.37,0.37,0.37);
   id<MTLRenderCommandEncoder> enc=[cb renderCommandEncoderWithDescriptor:write];
   if (!enc) { fprintf(stderr,"FAIL writer encoder\n"); return 1; }
   [enc setRenderPipelineState:pipelines[0]]; [enc setFragmentBytes:&c length:sizeof(c) atIndex:0];
   [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [enc endEncoding];
   // Store ends the first pass; the second pass must decode persisted codes.
   MTLRenderPassDescriptor *fetch=[MTLRenderPassDescriptor renderPassDescriptor];
   fetch.colorAttachments[0].texture=xr;fetch.colorAttachments[0].loadAction=MTLLoadActionLoad;fetch.colorAttachments[0].storeAction=MTLStoreActionStore;
   fetch.colorAttachments[1].texture=out;fetch.colorAttachments[1].loadAction=MTLLoadActionClear;fetch.colorAttachments[1].storeAction=MTLStoreActionStore;
   fetch.colorAttachments[1].clearColor=MTLClearColorMake(0.37,0.37,0.37,0.37);
   enc=[cb renderCommandEncoderWithDescriptor:fetch];
   if (!enc) { fprintf(stderr,"FAIL fetch encoder\n"); return 1; }
   [enc setRenderPipelineState:pipelines[1]];[enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[enc endEncoding];
   dispatch_semaphore_t done=dispatch_semaphore_create(0);
   [cb addCompletedHandler:^(id<MTLCommandBuffer> command){ (void)command; dispatch_semaphore_signal(done); }];
   [cb commit];
   if(dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,30LL*NSEC_PER_SEC))) {
    fprintf(stderr,"FAIL GPU command timeout case=%u\n",c);fflush(stderr);_exit(1);
   }
   if (cb.status != MTLCommandBufferStatusCompleted) { fprintf(stderr,"FAIL command case=%u %s\n",c,cb.error.localizedDescription.UTF8String); return 1; }
   std::vector<float> values(w*h*4, NAN);
   [out getBytes:values.data() bytesPerRow:w*16 fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
   unsigned bad=0; for (NSUInteger i=0;i<w*h;i++) for(unsigned j=0;j<4;j++) {
    float v=values[i*4+j], expected=xr_reference(c,unsigned(i%w),unsigned(i/w),j);
    if (!xr_equal(v,expected)) { bad++; if(bad==1) fprintf(stderr,"FAIL case=%u pixel=%lu lane=%u got=%.9g expected=%.9g\n",c,(unsigned long)i,j,v,expected); }
   }
   if(bad) return 1;
   printf("PASS XR550 store/load Metal fetch case=%u pixels=%lu color+alpha\n",c,(unsigned long)(w*h));
  }
  return 0;
 }
}
