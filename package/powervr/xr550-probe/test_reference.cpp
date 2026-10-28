#include "xr550_reference.h"
#include <cassert>
#include <cstdio>
int main() {
 unsigned bypass=0, permuted=0, cleared=0;
 for(unsigned y=0;y<32;y++)for(unsigned x=0;x<32;x++)for(unsigned lane=0;lane<4;lane++) {
  float expected=xr_reference(0,x,y,lane);
  assert(xr_equal(expected,expected));
  bypass += !xr_equal(half_value(xr_input(0,x,y,lane)),expected);
  permuted += !xr_equal(xr_reference(0,(x+1)%32,y,lane),expected);
  cleared += !xr_equal(0.37f,expected);
 }
 assert(bypass>3000 && permuted>2000 && cleared==4096);
 assert(xr_reference(1,0,0,0)==half_value(-384.0f/510.0f));
 assert(xr_reference(1,1,0,0)==half_value(638.0f/510.0f));
 assert(xr_reference(1,0,0,3)==0.0f && xr_reference(1,1,0,3)==1.0f);
 assert(!xr_equal(NAN,0.0f));
 printf("PASS bypass=%u permutation=%u missing-draw=%u/4096; XR and alpha boundaries\n",bypass,permuted,cleared);
}
