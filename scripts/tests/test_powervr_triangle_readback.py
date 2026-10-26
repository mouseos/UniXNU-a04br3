#!/usr/bin/env python3
"""実際の画素判定関数をホストCコンパイラで検証する。SDK・GPU不要。"""
from pathlib import Path
import os
import json
import shlex
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[2]
    source = root / "package/powervr/user/powervrprobed_vulkan.c"
    text = source.read_text()
    start = text.index("static int triangleReadbackCheck(")
    # 現在の関数直後の境界を使い、コピーした判定ロジックをテストしない。
    end = text.index("// Vertex + fragment execution", start)
    function = text[start:end]
    harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <assert.h>
enum { kWidth = 64, kHeight = 64 };
static int passes, failures;
static void probe_line(const char *format, ...) { (void)format; }
static int vkCheck(const char *name, int ok, const char *detail)
{
    (void)name; (void)detail;
    if (ok) passes++; else failures++;
    return ok;
}
''' + function + r'''
static uint8_t pixels[kWidth * kHeight * 4];
static void put(int x, int y, int triangle)
{
    uint8_t *p = &pixels[4 * (y * kWidth + x)];
    p[0] = triangle ? 255 : 0x22;
    p[1] = triangle ? 0 : 0x44;
    p[2] = triangle ? 255 : 0x66;
    p[3] = 255;
}
static void fill(int triangle)
{
    for (int y = 0; y < kHeight; y++)
        for (int x = 0; x < kWidth; x++)
            put(x, y, triangle && x >= 8 && y >= 8 && x + y < 63);
}
int main(void)
{
    fill(1);
    assert(triangleReadbackCheck(pixels, "ideal-triangle"));
    assert(passes == 2 && failures == 0);

    fill(0);
    const int directOK = triangleReadbackCheck(pixels, "linear-direct");
    assert(!directOK && passes == 3 && failures == 1);
    fill(1);
    const int copyOK = triangleReadbackCheck(pixels, "linear-copy");
    assert(copyOK && passes == 5 && failures == 1);
    assert(!(directOK && copyOK));
    // 成功したコピー判定でも先行する直接判定の失敗カウンタは残る。

    put(8, 8, 0);
    assert(!triangleReadbackCheck(pixels, "wrong-interior-pixel"));
    assert(passes == 6 && failures == 2);
    fill(1);
    pixels[0] = 0;
    assert(!triangleReadbackCheck(pixels, "wrong-background-pixel"));
    assert(passes == 7 && failures == 3);

    fill(1);
    for (int x = 8; x <= 55; x++) put(x, 63 - x, 1);
    assert(triangleReadbackCheck(pixels, "triangle-colored-edge"));
    assert(passes == 9 && failures == 3);
    // 既存仕様は対角48画素を判定から除外する。背景色の対角はidealで検証済み。
    for (int x = 8; x <= 55; x++) pixels[4 * ((63 - x) * kWidth + x)] = 0x7b;
    assert(triangleReadbackCheck(pixels, "excluded-edge"));
    assert(passes == 11 && failures == 3);
    puts("PASS: ideal triangle, background-only, wrong interior/background pixel, edge tolerance, retained direct failure");
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="powervr-triangle-") as temporary:
        c_file = Path(temporary) / "check.c"
        binary = Path(temporary) / "check"
        c_file.write_text(harness)
        compiler = shlex.split(os.environ.get("CC", "cc"))
        subprocess.run(compiler + ["-std=c11", "-Wall", "-Wextra", "-Werror",
                                  str(c_file), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
    test_copy_lifecycle(source)


def test_copy_lifecycle(source):
    """実際の通常copy helperをmock Vulkanで実行し、未書込みとtimeoutを検証する。"""
    harness = '#include ' + json.dumps(str(source)) + '\n' + r'''
#include <assert.h>
static uint8_t backing[16384];
static int timeout_wait, submit_error, write_pixels, flushes, host_barriers, destroys, idle_calls;
void probe_line(const char *f, ...) { (void)f; }
void probe_pass(void) {}
void probe_fail(void) {}
static VkResult create_buffer(VkDevice d,const VkBufferCreateInfo *i,const VkAllocationCallbacks *a,VkBuffer *b)
{ *b=(VkBuffer)(uintptr_t)1; return VK_SUCCESS; }
static void requirements(VkDevice d,VkBuffer b,VkMemoryRequirements *r)
{ *r=(VkMemoryRequirements){.size=sizeof(backing),.alignment=4096,.memoryTypeBits=1}; }
static VkResult allocate(VkDevice d,const VkMemoryAllocateInfo *i,const VkAllocationCallbacks *a,VkDeviceMemory *m)
{ *m=(VkDeviceMemory)(uintptr_t)2; return VK_SUCCESS; }
static VkResult bind_buffer(VkDevice d,VkBuffer b,VkDeviceMemory m,VkDeviceSize o) {return VK_SUCCESS;}
static VkResult map_memory(VkDevice d,VkDeviceMemory m,VkDeviceSize o,VkDeviceSize z,VkMemoryMapFlags f,void **p)
{ *p=backing; return VK_SUCCESS; }
static VkResult flush(VkDevice d,uint32_t n,const VkMappedMemoryRange *r)
{ assert(n==1 && r->offset==0 && r->size==VK_WHOLE_SIZE); flushes++; return VK_SUCCESS; }
static VkResult invalidate(VkDevice d,uint32_t n,const VkMappedMemoryRange *r) {return VK_SUCCESS;}
static VkResult commands(VkDevice d,const VkCommandBufferAllocateInfo *i,VkCommandBuffer *c)
{*c=(VkCommandBuffer)(uintptr_t)3;return VK_SUCCESS;}
static VkResult begin_command(VkCommandBuffer c,const VkCommandBufferBeginInfo *i){return VK_SUCCESS;}
static VkResult end_command(VkCommandBuffer c){return VK_SUCCESS;}
static void barrier(VkCommandBuffer c,VkPipelineStageFlags src,VkPipelineStageFlags dst,VkDependencyFlags f,
 uint32_t nm,const VkMemoryBarrier *m,uint32_t nb,const VkBufferMemoryBarrier *b,uint32_t ni,const VkImageMemoryBarrier *i)
{ if(src==VK_PIPELINE_STAGE_HOST_BIT && dst==VK_PIPELINE_STAGE_TRANSFER_BIT) {
 assert(nb==1 && b->srcAccessMask==VK_ACCESS_HOST_WRITE_BIT && b->dstAccessMask==VK_ACCESS_TRANSFER_WRITE_BIT);host_barriers++; } }
static void copy_image(VkCommandBuffer c,VkImage i,VkImageLayout l,VkBuffer b,uint32_t n,const VkBufferImageCopy *r)
{assert(n==1 && r->bufferOffset==0);}
static VkResult create_fence(VkDevice d,const VkFenceCreateInfo *i,const VkAllocationCallbacks *a,VkFence *f)
{*f=(VkFence)(uintptr_t)4;return VK_SUCCESS;}
static VkResult submit(VkQueue q,uint32_t n,const VkSubmitInfo *s,VkFence f)
{
 assert(host_barriers==1);
 for(unsigned i=0;i<sizeof(backing);i++) assert(backing[i]==0xcd);
 if(submit_error) return VK_ERROR_DEVICE_LOST;
 if(write_pixels) for(unsigned i=0;i<sizeof(backing);i+=4) {
 backing[i]=0x39;backing[i+1]=0x72;backing[i+2]=0xb4;backing[i+3]=0xe1; }
 return VK_SUCCESS;
}
static VkResult wait_fence(VkDevice d,uint32_t n,const VkFence *f,VkBool32 all,uint64_t ns)
{assert(ns==10000000000ull);return timeout_wait?VK_TIMEOUT:VK_SUCCESS;}
static VkResult idle(VkDevice d){idle_calls++;return VK_SUCCESS;}
static void destroy_fence(VkDevice d,VkFence f,const VkAllocationCallbacks *a){destroys++;}
static void unmap(VkDevice d,VkDeviceMemory m){destroys++;}
static void destroy_buffer(VkDevice d,VkBuffer b,const VkAllocationCallbacks *a){destroys++;}
static void free_memory(VkDevice d,VkDeviceMemory m,const VkAllocationCallbacks *a){destroys++;}
static void reset(void) {
 memset(&hostCopyPending,0,sizeof(hostCopyPending));
 timeout_wait=submit_error=write_pixels=flushes=host_barriers=destroys=idle_calls=0;
 // Stale successful data must be overwritten by the canary before submission.
 for(unsigned i=0;i<sizeof(backing);i+=4) {
 backing[i]=0x39;backing[i+1]=0x72;backing[i+2]=0xb4;backing[i+3]=0xe1; }
}
int main(void) {
 p_vkCreateBuffer=create_buffer;p_vkGetBufferMemoryRequirements=requirements;
 p_vkAllocateMemory=allocate;p_vkBindBufferMemory=bind_buffer;p_vkMapMemory=map_memory;
 p_vkFlushMappedMemoryRanges=flush;p_vkInvalidateMappedMemoryRanges=invalidate;
 p_vkAllocateCommandBuffers=commands;p_vkBeginCommandBuffer=begin_command;p_vkEndCommandBuffer=end_command;
 p_vkCmdPipelineBarrier=barrier;p_vkCmdCopyImageToBuffer=copy_image;
 p_vkCreateFence=create_fence;p_vkQueueSubmit=submit;p_vkWaitForFences=wait_fence;
 p_vkDeviceWaitIdle=idle;p_vkDestroyFence=destroy_fence;p_vkUnmapMemory=unmap;
 p_vkDestroyBuffer=destroy_buffer;p_vkFreeMemory=free_memory;
 VkPhysicalDeviceMemoryProperties memory={.memoryTypeCount=1};
 memory.memoryTypes[0].propertyFlags=VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT;
 VkDevice d=(VkDevice)(uintptr_t)5;VkQueue q=(VkQueue)(uintptr_t)6;
 VkCommandPool pool=(VkCommandPool)(uintptr_t)7;VkImage image=(VkImage)(uintptr_t)8;
 reset();
 assert(hostOrdinaryCopyReadback(d,q,pool,image,&memory)==0);
 assert(flushes==1 && destroys==4 && !idle_calls && !hostCopyPending.active);
 reset();write_pixels=1;
 assert(hostOrdinaryCopyReadback(d,q,pool,image,&memory)==1);
 assert(flushes==1 && destroys==4 && !idle_calls);
 reset();timeout_wait=1;
 assert(hostOrdinaryCopyReadback(d,q,pool,image,&memory)==-1);
 assert(!destroys && !idle_calls && hostCopyPending.active);
 assert(hostCopyPending.source==image && hostCopyPending.pool==pool && hostCopyPending.ordinaryMap==backing);
 assert(probe_vulkan("must-not-open-driver")==-1);
 reset();submit_error=1;
 assert(hostOrdinaryCopyReadback(d,q,pool,image,&memory)==-1);
 assert(!destroys && !idle_calls && hostCopyPending.active);
 reset();memory.memoryTypes[0].propertyFlags|=VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;write_pixels=1;
 assert(hostOrdinaryCopyReadback(d,q,pool,image,&memory)==1);
 assert(!flushes && destroys==4 && !idle_calls);
 puts("PASS: canary rejects stale success; flush/dependency; completed pixel failure; timeout/submit-error retention; quarantine; coherent policy");
 return 0;
}
'''
    # callerの所有資源を解放する分岐へpendingが流れないことも確認する。
    text = source.read_text()
    caller = text[text.index("static int hostImportProbe("):text.index('#include "powervrprobed_roa.h"')]
    assert "if (ordinaryOK < 0) { pending = 1; goto cleanup; }" in caller
    pending = caller[caller.index("cleanup:"):caller.index("if (fence) p_vkDestroyFence", caller.index("cleanup:"))]
    for field in ("sourceMemory", "hostPointer", "importedMemory", "pool"):
        assert "hostCopyPending." + field + " =" in pending
    assert "return -1;" in pending and "p_vkDeviceWaitIdle" not in caller
    outer = text[text.index("const int hostOK = hostImportProbe("):text.index("out_objects:", text.index("const int hostOK = hostImportProbe("))]
    assert outer.index("if (hostCopyPending.active)") < outer.index("return -1;")
    with tempfile.TemporaryDirectory(prefix="powervr-copy-lifecycle-") as temporary:
        c_file = Path(temporary) / "check.c"
        binary = Path(temporary) / "check"
        c_file.write_text(harness)
        compiler = shlex.split(os.environ.get("CC", "cc"))
        subprocess.run(compiler + ["-std=c11", "-D_POSIX_C_SOURCE=200809L", "-Wall", "-Wextra",
                                  "-Werror", "-Wno-unused-parameter", str(c_file), "-ldl", "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
