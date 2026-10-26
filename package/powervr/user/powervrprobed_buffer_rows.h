/* SPDX-License-Identifier: MIT
 * Owner-only GPU row-copy qualification. No CPU destination payload copy. */
#include <time.h>
/* Host wall time only: submit can itself wait for preceding GPU work in winsys.
 * These values are not GPU timestamps and must not be presented as GPU duration. */
static uint64_t brClockUs(void)
{
  struct timespec t;
  if(clock_gettime(CLOCK_MONOTONIC,&t)!=0)return UINT64_MAX;
  return (uint64_t)t.tv_sec*1000000u+(uint64_t)t.tv_nsec/1000u;
}
static uint64_t brElapsedUs(uint64_t start,uint64_t end)
{
  return start!=UINT64_MAX && end!=UINT64_MAX && end>=start ? end-start : 0;
}
enum { brRows=1280, brWidth=3152, brSrcPitch=3328, brDstPitch=3200, brPrefix=4096 };
static uint8_t brPattern(size_t i) { return (uint8_t)((i*73u) ^ (i>>8) ^ (i>>16) ^ 0x3du); }
static int brPayload(size_t i, size_t *source)
{
  if(i<brPrefix)return 0;
  size_t p=i-brPrefix, y=p/brDstPitch, x=p%brDstPitch;
  if(y>=brRows || x>=brWidth)return 0;
  *source=brPrefix+y*brSrcPitch+x; return 1;
}
static int brValidate(const volatile uint8_t *source, size_t sourceSize,
                      const volatile uint8_t *destination, size_t destinationSize)
{
  size_t counts[3]={0}, bad[3]={0};
  for(size_t n=0;n<destinationSize;n++) {
    size_t src=0; int payload=brPayload(n,&src); unsigned category=payload?0:1;
    uint8_t expected=payload?brPattern(src):0xa5;
    counts[category]++; if(destination[n]!=expected)bad[category]++;
  }
  for(size_t n=0;n<sourceSize;n++){counts[2]++;if(source[n]!=brPattern(n))bad[2]++;}
  int ok=1;
  const char *names[]={"rows payload","rows all padding/prefix/allocation-tail","rows source unchanged"};
  for(unsigned i=0;i<3;i++) {
    char detail[128];snprintf(detail,sizeof detail,"checked=%zu mismatches=%zu",counts[i],bad[i]);
    ok &= vkCheck(names[i],bad[i]==0,detail);
  }
  return ok;
}
/* Returns -1 only when submitted work has no confirmed completion: retain everything. */
static int bufferRowsProbe(VkDevice device, VkQueue queue, uint32_t family,
                           const VkPhysicalDeviceMemoryProperties *memory)
{
  VkBuffer buffers[2]={0}; VkDeviceMemory allocations[2]={0};
  void *mapped[2]={0}; VkMemoryRequirements needs[2]={{0}};
  VkCommandPool pool=0; VkFence fence=0; int ok=0;
#define BR_STEP(name, call) do { if(!vkStep(name,(call),NULL))goto done; } while(0)
  probe_line("vk buffer-rows: rows=%u width=%u srcPitch=%u dstPitch=%u prefix=%u",brRows,brWidth,brSrcPitch,brDstPitch,brPrefix);
  for(unsigned i=0;i<2;i++) {
    VkDeviceSize bytes=(brPrefix+(VkDeviceSize)(i?brDstPitch:brSrcPitch)*brRows+4096+4095)&~(VkDeviceSize)4095;
    VkBufferCreateInfo info={.sType=VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,.size=bytes,
      .usage=i?VK_BUFFER_USAGE_TRANSFER_DST_BIT:VK_BUFFER_USAGE_TRANSFER_SRC_BIT,.sharingMode=VK_SHARING_MODE_EXCLUSIVE};
    BR_STEP("rows create buffer",p_vkCreateBuffer(device,&info,NULL,&buffers[i]));
    p_vkGetBufferMemoryRequirements(device,buffers[i],&needs[i]);
    if(!vkCheck("rows allocation bound",needs[i].size>=bytes && needs[i].size<=64u*1024u*1024u,NULL))goto done;
    uint32_t type=memoryType(memory,needs[i].memoryTypeBits,VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if(type==UINT32_MAX)type=memoryType(memory,needs[i].memoryTypeBits,VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT);
    if(!vkCheck("rows host memory",type!=UINT32_MAX,NULL))goto done;
    VkMemoryAllocateInfo alloc={.sType=VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,.allocationSize=needs[i].size,.memoryTypeIndex=type};
    BR_STEP("rows allocate",p_vkAllocateMemory(device,&alloc,NULL,&allocations[i]));
    BR_STEP("rows bind",p_vkBindBufferMemory(device,buffers[i],allocations[i],0));
    BR_STEP("rows map",p_vkMapMemory(device,allocations[i],0,VK_WHOLE_SIZE,0,&mapped[i]));
    for(size_t n=0;n<needs[i].size;n++)((uint8_t *)mapped[i])[n]=i?0xa5:brPattern(n);
    probe_line("vk buffer-rows allocation %u requested=%llu actual=%llu",i,(unsigned long long)bytes,(unsigned long long)needs[i].size);
  }
  VkMappedMemoryRange ranges[2];
  for(unsigned i=0;i<2;i++)ranges[i]=(VkMappedMemoryRange){.sType=VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,.memory=allocations[i],.size=VK_WHOLE_SIZE};
  BR_STEP("rows flush",p_vkFlushMappedMemoryRanges(device,2,ranges));
  VkCommandPoolCreateInfo pi={.sType=VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,.queueFamilyIndex=family};
  BR_STEP("rows pool",p_vkCreateCommandPool(device,&pi,NULL,&pool));
  VkCommandBuffer cmd;
  VkCommandBufferAllocateInfo ai={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,.commandPool=pool,.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY,.commandBufferCount=1};
  BR_STEP("rows command",p_vkAllocateCommandBuffers(device,&ai,&cmd));
  VkCommandBufferBeginInfo bi={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
  BR_STEP("rows begin",p_vkBeginCommandBuffer(cmd,&bi));
  const uint64_t recordStart=brClockUs();
  VkBufferMemoryBarrier barriers[2];
  for(unsigned i=0;i<2;i++)barriers[i]=(VkBufferMemoryBarrier){.sType=VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
    .srcAccessMask=VK_ACCESS_HOST_WRITE_BIT,.dstAccessMask=i?VK_ACCESS_TRANSFER_WRITE_BIT:VK_ACCESS_TRANSFER_READ_BIT,
    .srcQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,.buffer=buffers[i],.size=VK_WHOLE_SIZE};
  p_vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_HOST_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,0,NULL,2,barriers,0,NULL);
  VkBufferCopy regions[brRows];
  for(unsigned y=0;y<brRows;y++)regions[y]=(VkBufferCopy){brPrefix+(VkDeviceSize)y*brSrcPitch,brPrefix+(VkDeviceSize)y*brDstPitch,brWidth};
  p_vkCmdCopyBuffer(cmd,buffers[0],buffers[1],brRows,regions);
  for(unsigned i=0;i<2;i++){barriers[i].srcAccessMask=i?VK_ACCESS_TRANSFER_WRITE_BIT:VK_ACCESS_TRANSFER_READ_BIT;barriers[i].dstAccessMask=VK_ACCESS_HOST_READ_BIT;}
  p_vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,0,NULL,2,barriers,0,NULL);
  const VkResult endResult=p_vkEndCommandBuffer(cmd);
  const uint64_t recordEnd=brClockUs();
  BR_STEP("rows end",endResult);
  VkFenceCreateInfo fi={.sType=VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
  BR_STEP("rows fence",p_vkCreateFence(device,&fi,NULL,&fence));
  VkSubmitInfo si={.sType=VK_STRUCTURE_TYPE_SUBMIT_INFO,.commandBufferCount=1,.pCommandBuffers=&cmd};
  const uint64_t submitStart=brClockUs();
  VkResult submitted=p_vkQueueSubmit(queue,1,&si,fence);
  const uint64_t submitEnd=brClockUs();
  if(!vkStep("rows submit",submitted,NULL)) {
    if(submitted==VK_ERROR_DEVICE_LOST){probe_line("vk buffer-rows retained: device lost at submit");return -1;}
    goto done;
  }
  const uint64_t waitStart=brClockUs();
  const VkResult waited=p_vkWaitForFences(device,1,&fence,VK_TRUE,30000000000ULL);
  const uint64_t waitEnd=brClockUs();
  if(!vkStep("rows wait",waited,NULL)) {
    probe_line("vk buffer-rows retained: completion unknown");return -1;
  }
  BR_STEP("rows invalidate",p_vkInvalidateMappedMemoryRanges(device,2,ranges));
  const uint64_t validateStart=brClockUs();
  ok=brValidate(mapped[0],(size_t)needs[0].size,mapped[1],(size_t)needs[1].size);
  const uint64_t validateEnd=brClockUs();
  const int clockOK=recordStart!=UINT64_MAX && recordEnd>=recordStart && recordEnd!=UINT64_MAX &&
    submitStart!=UINT64_MAX && submitEnd>=submitStart && submitEnd!=UINT64_MAX &&
    waitStart!=UINT64_MAX && waitEnd>=waitStart && waitEnd!=UINT64_MAX &&
    validateStart!=UINT64_MAX && validateEnd>=validateStart && validateEnd!=UINT64_MAX;
  probe_line("vk buffer-rows host-us: record=%llu submit=%llu wait=%llu validate=%llu clock=%d",
    (unsigned long long)brElapsedUs(recordStart,recordEnd),
    (unsigned long long)brElapsedUs(submitStart,submitEnd),
    (unsigned long long)brElapsedUs(waitStart,waitEnd),
    (unsigned long long)brElapsedUs(validateStart,validateEnd),clockOK);
done:
  if(fence)p_vkDestroyFence(device,fence,NULL);
  if(pool)p_vkDestroyCommandPool(device,pool,NULL);
  for(unsigned i=0;i<2;i++) {
    if(mapped[i])p_vkUnmapMemory(device,allocations[i]);
    if(buffers[i])p_vkDestroyBuffer(device,buffers[i],NULL);
    if(allocations[i])p_vkFreeMemory(device,allocations[i],NULL);
  }
#undef BR_STEP
  return ok;
}
