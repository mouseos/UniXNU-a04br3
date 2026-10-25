// SPDX-License-Identifier: MIT
// Included by powervrprobed_vulkan.c; opt-in owner test, not a driver workaround.
#include "powervrprobed_roa_spv.h"

static int roaSupported(VkPhysicalDevice physical)
{
  uint32_t count=0;
  if (!vkStep("ROA extension count",p_vkEnumerateDeviceExtensionProperties(physical,NULL,&count,NULL),NULL)) return 0;
  VkExtensionProperties *ext=calloc(count?count:1,sizeof(*ext));
  if (!vkCheck("ROA extension allocation",ext!=NULL,NULL)) return 0;
  VkResult status=p_vkEnumerateDeviceExtensionProperties(physical,NULL,&count,ext);
  int found=0;
  if(status==VK_SUCCESS)for(uint32_t i=0;i<count;i++)
    if(!strcmp(ext[i].extensionName,VK_EXT_RASTERIZATION_ORDER_ATTACHMENT_ACCESS_EXTENSION_NAME))found=1;
  free(ext);
  if(!vkStep("ROA extension enumeration",status,NULL) || !vkCheck("ROA COLOR extension",found,NULL))return 0;
  VkPhysicalDeviceRasterizationOrderAttachmentAccessFeaturesEXT roa={
    .sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_RASTERIZATION_ORDER_ATTACHMENT_ACCESS_FEATURES_EXT};
  VkPhysicalDeviceFeatures2 features={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,.pNext=&roa};
  p_vkGetPhysicalDeviceFeatures2(physical,&features);
  VkPhysicalDeviceProperties properties;
  p_vkGetPhysicalDeviceProperties(physical,&properties);
  probe_line("ROA advertised color=%u depth=%u stencil=%u",roa.rasterizationOrderColorAttachmentAccess,
             roa.rasterizationOrderDepthAttachmentAccess,roa.rasterizationOrderStencilAttachmentAccess);
  return vkCheck("ROA COLOR feature",roa.rasterizationOrderColorAttachmentAccess &&
                 (properties.vendorID!=0x1010 || (!roa.rasterizationOrderDepthAttachmentAccess && !roa.rasterizationOrderStencilAttachmentAccess)),
                 "PowerVR prototype must expose COLOR only");
}

// All pixels are covered by one oversized triangle, repeated in ONE draw.
// Case 0: 128 increments ->128. Case 1: nonfetch writer 7 then ->135.
// Case 2: old*33+instance+1 (mod 2^32) proves primitive order as well as exclusion.
static int roaProbe(VkDevice device,VkQueue queue,VkCommandPool pool,VkImage image,VkBuffer buffer,
                    VkDeviceMemory allocation,void *mapped,int coherent)
{
  VkImageView view=VK_NULL_HANDLE;
  VkRenderPass pass=VK_NULL_HANDLE;
  VkFramebuffer fb=VK_NULL_HANDLE;
  VkDescriptorSetLayout setLayout=VK_NULL_HANDLE;
  VkDescriptorPool descriptors=VK_NULL_HANDLE;
  VkPipelineLayout layout=VK_NULL_HANDLE;
  VkShaderModule shader[3]={VK_NULL_HANDLE,VK_NULL_HANDLE,VK_NULL_HANDLE};
  VkPipeline pipelines[3]={VK_NULL_HANDLE,VK_NULL_HANDLE,VK_NULL_HANDLE};
  VkFence fence=VK_NULL_HANDLE;
  int ok=0;
#define ROA_STEP(name,expr) do {if(!vkStep("ROA " name,(expr),NULL))goto cleanup;}while(0)
  const VkImageViewCreateInfo vi={.sType=VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,.image=image,
    .viewType=VK_IMAGE_VIEW_TYPE_2D,.format=VK_FORMAT_R32_UINT,.subresourceRange={VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1}};
  ROA_STEP("view",p_vkCreateImageView(device,&vi,NULL,&view));
  const VkAttachmentDescription attachment={.format=VK_FORMAT_R32_UINT,.samples=VK_SAMPLE_COUNT_1_BIT,
    .loadOp=VK_ATTACHMENT_LOAD_OP_CLEAR,.storeOp=VK_ATTACHMENT_STORE_OP_STORE,
    .stencilLoadOp=VK_ATTACHMENT_LOAD_OP_DONT_CARE,.stencilStoreOp=VK_ATTACHMENT_STORE_OP_DONT_CARE,
    .initialLayout=VK_IMAGE_LAYOUT_GENERAL,.finalLayout=VK_IMAGE_LAYOUT_GENERAL};
  const VkAttachmentReference ref={0,VK_IMAGE_LAYOUT_GENERAL};
  const VkSubpassDescription sub={.flags=VK_SUBPASS_DESCRIPTION_RASTERIZATION_ORDER_ATTACHMENT_COLOR_ACCESS_BIT_EXT,
    .pipelineBindPoint=VK_PIPELINE_BIND_POINT_GRAPHICS,.inputAttachmentCount=1,.pInputAttachments=&ref,
    .colorAttachmentCount=1,.pColorAttachments=&ref};
  const VkRenderPassCreateInfo pi={.sType=VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO,.attachmentCount=1,
    .pAttachments=&attachment,.subpassCount=1,.pSubpasses=&sub};
  ROA_STEP("pass",p_vkCreateRenderPass(device,&pi,NULL,&pass));
  const VkFramebufferCreateInfo fi={.sType=VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO,.renderPass=pass,
    .attachmentCount=1,.pAttachments=&view,.width=kWidth,.height=kHeight,.layers=1};
  ROA_STEP("framebuffer",p_vkCreateFramebuffer(device,&fi,NULL,&fb));
  const VkDescriptorSetLayoutBinding binding={.binding=0,.descriptorType=VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT,
    .descriptorCount=1,.stageFlags=VK_SHADER_STAGE_FRAGMENT_BIT};
  const VkDescriptorSetLayoutCreateInfo si={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
    .bindingCount=1,.pBindings=&binding};
  ROA_STEP("set layout",p_vkCreateDescriptorSetLayout(device,&si,NULL,&setLayout));
  const VkPipelineLayoutCreateInfo li={.sType=VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,.setLayoutCount=1,.pSetLayouts=&setLayout};
  ROA_STEP("pipeline layout",p_vkCreatePipelineLayout(device,&li,NULL,&layout));
  const VkDescriptorPoolSize size={VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT,1};
  const VkDescriptorPoolCreateInfo dpi={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,.maxSets=1,.poolSizeCount=1,.pPoolSizes=&size};
  ROA_STEP("descriptor pool",p_vkCreateDescriptorPool(device,&dpi,NULL,&descriptors));
  VkDescriptorSet set=VK_NULL_HANDLE;
  const VkDescriptorSetAllocateInfo sai={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,.descriptorPool=descriptors,
    .descriptorSetCount=1,.pSetLayouts=&setLayout};
  ROA_STEP("descriptor set",p_vkAllocateDescriptorSets(device,&sai,&set));
  const VkDescriptorImageInfo ii={.imageView=view,.imageLayout=VK_IMAGE_LAYOUT_GENERAL};
  const VkWriteDescriptorSet write={.sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,.dstSet=set,.dstBinding=0,
    .descriptorCount=1,.descriptorType=VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT,.pImageInfo=&ii};
  p_vkUpdateDescriptorSets(device,1,&write,0,NULL);
  const uint32_t *codes[]={kRoaVertSpv,kRoaFragSpv,kRoaWriterSpv};
  const size_t lengths[]={sizeof(kRoaVertSpv),sizeof(kRoaFragSpv),sizeof(kRoaWriterSpv)};
  for(unsigned i=0;i<3;i++){
    const VkShaderModuleCreateInfo sm={.sType=VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,.codeSize=lengths[i],.pCode=codes[i]};
    ROA_STEP("shader",p_vkCreateShaderModule(device,&sm,NULL,&shader[i]));
  }
  const VkPipelineVertexInputStateCreateInfo input={.sType=VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO};
  const VkPipelineInputAssemblyStateCreateInfo assembly={.sType=VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO,
    .topology=VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST};
  const VkViewport viewport={0,0,kWidth,kHeight,0,1};
  const VkRect2D scissor={{0,0},{kWidth,kHeight}};
  const VkPipelineViewportStateCreateInfo vs={.sType=VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO,
    .viewportCount=1,.pViewports=&viewport,.scissorCount=1,.pScissors=&scissor};
  const VkPipelineRasterizationStateCreateInfo raster={.sType=VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO,
    .polygonMode=VK_POLYGON_MODE_FILL,.cullMode=VK_CULL_MODE_NONE,.frontFace=VK_FRONT_FACE_COUNTER_CLOCKWISE,.lineWidth=1};
  const VkPipelineMultisampleStateCreateInfo ms={.sType=VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO,.rasterizationSamples=VK_SAMPLE_COUNT_1_BIT};
  const VkPipelineColorBlendAttachmentState ba={.colorWriteMask=VK_COLOR_COMPONENT_R_BIT};
  const VkPipelineColorBlendStateCreateInfo blend={.sType=VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO,
    .flags=VK_PIPELINE_COLOR_BLEND_STATE_CREATE_RASTERIZATION_ORDER_ATTACHMENT_ACCESS_BIT_EXT,.attachmentCount=1,.pAttachments=&ba};
  for(unsigned i=0;i<3;i++){
    uint32_t ordered=i==2;
    const VkSpecializationMapEntry entry={.constantID=0,.offset=0,.size=sizeof(ordered)};
    const VkSpecializationInfo spec={.mapEntryCount=1,.pMapEntries=&entry,.dataSize=sizeof(ordered),.pData=&ordered};
    const VkPipelineShaderStageCreateInfo stages[]={
      {.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,.stage=VK_SHADER_STAGE_VERTEX_BIT,.module=shader[0],.pName="main"},
      {.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,.stage=VK_SHADER_STAGE_FRAGMENT_BIT,.module=shader[i==1?2:1],
       .pName="main",.pSpecializationInfo=i==1?NULL:&spec}};
    const VkGraphicsPipelineCreateInfo gp={.sType=VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO,.stageCount=2,.pStages=stages,
      .pVertexInputState=&input,.pInputAssemblyState=&assembly,.pViewportState=&vs,.pRasterizationState=&raster,
      .pMultisampleState=&ms,.pColorBlendState=&blend,.layout=layout,.renderPass=pass};
    ROA_STEP("pipeline",p_vkCreateGraphicsPipelines(device,VK_NULL_HANDLE,1,&gp,NULL,&pipelines[i]));
  }
  for(unsigned test=0;test<3;test++){
    VkCommandBuffer cmd=VK_NULL_HANDLE;
    const VkCommandBufferAllocateInfo ca={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,.commandPool=pool,
      .level=VK_COMMAND_BUFFER_LEVEL_PRIMARY,.commandBufferCount=1};
    ROA_STEP("command",p_vkAllocateCommandBuffers(device,&ca,&cmd));
    const VkCommandBufferBeginInfo cb={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    ROA_STEP("begin",p_vkBeginCommandBuffer(cmd,&cb));
    imageBarrier(cmd,image,test?VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL:VK_IMAGE_LAYOUT_UNDEFINED,VK_IMAGE_LAYOUT_GENERAL,
      test?VK_ACCESS_TRANSFER_READ_BIT:0,VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT|VK_ACCESS_INPUT_ATTACHMENT_READ_BIT,
      test?VK_PIPELINE_STAGE_TRANSFER_BIT:VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
      VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT|VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT);
    const VkBufferMemoryBarrier reuse={.sType=VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
      .srcAccessMask=test?VK_ACCESS_HOST_READ_BIT:0,.dstAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT,
      .srcQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,
      .buffer=buffer,.offset=0,.size=VK_WHOLE_SIZE};
    p_vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_HOST_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,0,NULL,1,&reuse,0,NULL);
    const VkClearValue clear={.color={.uint32={0,0,0,0}}};
    const VkRenderPassBeginInfo rb={.sType=VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO,.renderPass=pass,.framebuffer=fb,
      .renderArea={{0,0},{kWidth,kHeight}},.clearValueCount=1,.pClearValues=&clear};
    p_vkCmdBeginRenderPass(cmd,&rb,VK_SUBPASS_CONTENTS_INLINE);
    p_vkCmdBindDescriptorSets(cmd,VK_PIPELINE_BIND_POINT_GRAPHICS,layout,0,1,&set,0,NULL);
    if(test==1){p_vkCmdBindPipeline(cmd,VK_PIPELINE_BIND_POINT_GRAPHICS,pipelines[1]);p_vkCmdDraw(cmd,3,1,0,0);}
    p_vkCmdBindPipeline(cmd,VK_PIPELINE_BIND_POINT_GRAPHICS,pipelines[test==2?2:0]);
    p_vkCmdDraw(cmd,3,128,0,0); // No per-primitive/draw barriers: real overlap requirement.
    p_vkCmdEndRenderPass(cmd);
    imageBarrier(cmd,image,VK_IMAGE_LAYOUT_GENERAL,VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
      VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,VK_ACCESS_TRANSFER_READ_BIT,
      VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT);
    const VkBufferImageCopy copy={.imageSubresource={VK_IMAGE_ASPECT_COLOR_BIT,0,0,1},.imageExtent={kWidth,kHeight,1}};
    p_vkCmdCopyImageToBuffer(cmd,image,VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,buffer,1,&copy);
    const VkBufferMemoryBarrier host={.sType=VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,.srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT,
      .dstAccessMask=VK_ACCESS_HOST_READ_BIT,.srcQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,
      .buffer=buffer,.offset=0,.size=VK_WHOLE_SIZE};
    p_vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,0,NULL,1,&host,0,NULL);
    ROA_STEP("end",p_vkEndCommandBuffer(cmd));
    const VkFenceCreateInfo fc={.sType=VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    ROA_STEP("fence",p_vkCreateFence(device,&fc,NULL,&fence));
    const VkSubmitInfo submit={.sType=VK_STRUCTURE_TYPE_SUBMIT_INFO,.commandBufferCount=1,.pCommandBuffers=&cmd};
    ROA_STEP("submit",p_vkQueueSubmit(queue,1,&submit,fence));
    ROA_STEP("wait",p_vkWaitForFences(device,1,&fence,VK_TRUE,10000000000ULL));
    p_vkDestroyFence(device,fence,NULL);fence=VK_NULL_HANDLE;
    if(!coherent){const VkMappedMemoryRange range={.sType=VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,.memory=allocation,.size=VK_WHOLE_SIZE};
      ROA_STEP("invalidate",p_vkInvalidateMappedMemoryRanges(device,1,&range));}
    uint32_t expected=test==1?135:128;
    if(test==2){expected=0;for(uint32_t i=0;i<128;i++)expected=expected*33u+i+1u;}
    unsigned matched=0;uint32_t first=0;
    for(unsigned i=0;i<kWidth*kHeight;i++){uint32_t got=((volatile uint32_t*)mapped)[i];if(got==expected)matched++;else if(matched==i)first=got;}
    char detail[160];snprintf(detail,sizeof detail,"case=%u one draw instances=128 matched=%u/%u expected=%u first_bad=%u",
      test,matched,kWidth*kHeight,expected,first);
    if(!vkCheck("ROA overlap exact readback",matched==kWidth*kHeight,detail))goto cleanup;
  }
  ok=1;
cleanup:
  // Even a failed wait may leave work pending: keep all descriptors/pages alive.
  if(fence)p_vkDeviceWaitIdle(device);
  if(fence)p_vkDestroyFence(device,fence,NULL);
  for(unsigned i=0;i<3;i++)if(pipelines[i])p_vkDestroyPipeline(device,pipelines[i],NULL);
  for(unsigned i=0;i<3;i++)if(shader[i])p_vkDestroyShaderModule(device,shader[i],NULL);
  if(descriptors)p_vkDestroyDescriptorPool(device,descriptors,NULL);
  if(layout)p_vkDestroyPipelineLayout(device,layout,NULL);
  if(setLayout)p_vkDestroyDescriptorSetLayout(device,setLayout,NULL);
  if(fb)p_vkDestroyFramebuffer(device,fb,NULL);
  if(pass)p_vkDestroyRenderPass(device,pass,NULL);
  if(view)p_vkDestroyImageView(device,view,NULL);
  return ok;
#undef ROA_STEP
}
