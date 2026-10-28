#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
// Independent CPU model for an unblended write followed by STORE and LOAD.
// Linear XR is encoded after rounding the fragment output to half. Alpha is
// an 8-bit UNORM code. A tile load decodes and rounds each lane to half.
static inline float half_value(float v) { return (float)(_Float16)v; }
static inline float xr_input(unsigned test,unsigned x,unsigned y,unsigned lane) {
 if (test == 0) {
  switch(lane) {
   case 0:return (float(int((x+3*y)%64)-32)+0.4f)/510.0f;
   case 1:return (float(int((3*x+y)%64)-32)+0.4f)/510.0f;
   case 2:return (float(int((5*x+7*y)%64)-32)+0.4f)/510.0f;
   default:return (float(16+(7*x+11*y)%40)+0.4f)/255.0f;
  }
 }
 switch(lane) {
  case 0:return (x&1)?1.5f:-1.0f;
  case 1:return (y&1)?1.0f:0.0f;
  case 2:return ((x+y)&1)?0.5f:-0.5f;
  default:return (x&1)?1.5f:-0.5f;
 }
}
static inline float xr_reference(unsigned test,unsigned x,unsigned y,unsigned lane) {
 float v=half_value(xr_input(test,x,y,lane));
 if(lane==3) {
  v=std::max(0.0f,std::min(1.0f,v));
  float code=std::nearbyint(v*255.0f);
  return half_value(code/255.0f);
 }
 float code=std::nearbyint(v*510.0f)+384.0f;
 code=std::max(0.0f,std::min(1022.0f,code));
 return half_value((code-384.0f)/510.0f);
}
static inline bool xr_equal(float actual,float expected) {
 return std::isfinite(actual) && std::fabs(actual-expected)<0.00001f;
}
