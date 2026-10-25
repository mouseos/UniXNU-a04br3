// SPDX-License-Identifier: MIT
#version 450
// With a positive 64x64 viewport these become (8,8), (56,8), (8,56).
const vec2 positions[3] = vec2[](vec2(-0.75, -0.75), vec2(0.75, -0.75), vec2(-0.75, 0.75));
void main()
{
    gl_Position = vec4(positions[gl_VertexIndex], 0.5, 1.0);
}
