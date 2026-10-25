// SPDX-License-Identifier: MIT
#version 450
layout(input_attachment_index=0,set=0,binding=0) uniform usubpassInput prior;
layout(location=0) flat in uint serial;
layout(location=0) out uint value;
layout(constant_id=0) const uint ordered=0;
void main(){uint old=subpassLoad(prior).r;value=ordered!=0 ? old*33u+serial+1u : old+1u;}
