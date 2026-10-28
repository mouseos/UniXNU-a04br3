#include <metal_stdlib>
using namespace metal;
struct Vertex { float4 position [[position]]; };
vertex Vertex xr_vertex(uint id [[vertex_id]]) {
    const float2 p[3] = {float2(-1,-1), float2(3,-1), float2(-1,3)};
    return {float4(p[id],0,1)};
}
struct Outputs { float4 xr [[color(0)]]; float4 observed [[color(1)]]; };
fragment Outputs xr_write(constant float4 &value [[buffer(0)]]) {
    return {value, float4(-99)};
}
fragment Outputs xr_fetch(float4 prior [[color(0)]]) {
    return {prior, prior};
}
