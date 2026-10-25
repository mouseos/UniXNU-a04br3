// SPDX-License-Identifier: MIT
#version 450
layout(location=0) flat out uint serial;
const vec2 positions[3]=vec2[](vec2(-1,-1),vec2(3,-1),vec2(-1,3));
void main(){gl_Position=vec4(positions[gl_VertexIndex],0.5,1);serial=uint(gl_InstanceIndex);}
