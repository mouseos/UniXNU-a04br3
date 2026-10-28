#include <metal_stdlib>
using namespace metal;
struct Vertex { float4 position [[position]]; };
vertex Vertex xr_vertex(uint id [[vertex_id]]) {
    const float2 p[3] = {float2(-1,-1), float2(3,-1), float2(-1,3)};
    return {float4(p[id],0,1)};
}
struct Outputs { float4 xr [[color(0)]]; float4 observed [[color(1)]]; };
fragment Outputs xr_write(float4 pos [[position]], constant uint &test [[buffer(0)]]) {
    uint x=uint(pos.x), y=uint(pos.y);
    float4 v;
    if (test == 0) {
        v=float4((float(int((x+3*y)%64)-32)+0.4f)/510.0f,
                 (float(int((3*x+y)%64)-32)+0.4f)/510.0f,
                 (float(int((5*x+7*y)%64)-32)+0.4f)/510.0f,
                 (float(16+(7*x+11*y)%40)+0.4f)/255.0f);
    } else {
        v=float4((x&1) ? 1.5f : -1.0f, (y&1) ? 1.0f : 0.0f,
                 ((x+y)&1) ? 0.5f : -0.5f, (x&1) ? 1.5f : -0.5f);
    }
    return {v, float4(-99)};
}
fragment Outputs xr_fetch(float4 prior [[color(0)]]) { return {prior, prior}; }
