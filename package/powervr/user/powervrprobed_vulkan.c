// SPDX-License-Identifier: MIT
// powervrprobed, the Mesa stage (built with POWERVRPROBED_VULKAN=1 by scripts/build-a04br3-powervr-probe.py --vulkan):
// the PowerVR Vulkan driver of scripts/build-pvr-darwin.py (libvulkan_powervr_mesa.dylib with the darwin winsys of
// patches/mesa/pvr-darwin-winsys.patch) first tests direct linear rendering when supported,
// then requires a transfer clear/readback and an optimal-image triangle/readback.
//
//   dlopen(library) -> vkGetInstanceProcAddr (the darwin patch exports it, pvr_instance.c; there is no Khronos loader)
//   vkCreateInstance -> vkEnumeratePhysicalDevices (the darwin winsys opens NativePowerVRGpu) -> vkCreateDevice (one queue)
//   64x64 VK_FORMAT_R8G8B8A8_UNORM image, optimal tiling -> UNDEFINED to TRANSFER_DST -> vkCmdClearColorImage ->
//   TRANSFER_DST to TRANSFER_SRC -> vkCmdCopyImageToBuffer into a host-visible buffer prefilled with 0xdeadbeef ->
//   vkQueueSubmit with a fence -> vkWaitForFences -> compare every pixel.
// The clear and the copy are Mesa transfer jobs (pvr_arch_job_transfer.c), so the GPU runs command streams and PDS/USC
// programs that Mesa generated, through SubmitJobs and WaitFence of the user client.
//
// The library is opened at run time: the daemon has no link-time dependency on it and a build without --vulkan leaves this
// file out. Mesa refuses devices it does not list as conformant unless PVR_I_WANT_A_BROKEN_VULKAN_DRIVER is set
// (pvr_physical_device.c, pvr_device_is_conformant()); the probe sets it for its own process.
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define VK_NO_PROTOTYPES 1
#include <vulkan/vulkan.h>

#include "powervrprobed.h"
#include "powervrprobed_triangle_spv.h"

enum { kWidth = 64, kHeight = 64 };
// Exactly representable in UNORM8: k / 255 converts back to k.
static const uint8_t kClear[4] = {0x11, 0x5a, 0xa5, 0xee};

#define VK_FUNCTIONS(X)                                                                                                       \
  X(vkDestroyInstance) X(vkEnumeratePhysicalDevices) X(vkGetPhysicalDeviceProperties) X(vkGetPhysicalDeviceProperties2)    \
  X(vkGetPhysicalDeviceQueueFamilyProperties) X(vkGetPhysicalDeviceMemoryProperties) X(vkGetPhysicalDeviceFormatProperties) \
  X(vkEnumerateDeviceExtensionProperties) X(vkGetPhysicalDeviceExternalBufferProperties)                              \
  X(vkCreateDevice) X(vkGetDeviceProcAddr) X(vkGetPhysicalDeviceFeatures2)
#define VK_DEVICE_FUNCTIONS(X)                                                                                                \
  X(vkDestroyDevice) X(vkGetDeviceQueue) X(vkCreateImage) X(vkDestroyImage) X(vkGetImageMemoryRequirements)                  \
  X(vkCreateBuffer) X(vkDestroyBuffer) X(vkGetBufferMemoryRequirements) X(vkAllocateMemory) X(vkFreeMemory)                 \
  X(vkBindImageMemory) X(vkBindBufferMemory) X(vkMapMemory) X(vkUnmapMemory) X(vkInvalidateMappedMemoryRanges)                \
  X(vkGetImageSubresourceLayout) X(vkFlushMappedMemoryRanges)                                                            \
  X(vkCreateCommandPool) X(vkDestroyCommandPool) X(vkAllocateCommandBuffers) X(vkBeginCommandBuffer) X(vkEndCommandBuffer)   \
  X(vkCmdPipelineBarrier) X(vkCmdClearColorImage) X(vkCmdCopyImageToBuffer) X(vkCreateFence) X(vkDestroyFence)              \
  X(vkQueueSubmit) X(vkWaitForFences) X(vkDeviceWaitIdle)                                                            \
  X(vkCreateImageView) X(vkDestroyImageView) X(vkCreateRenderPass) X(vkDestroyRenderPass)                                   \
  X(vkCreateFramebuffer) X(vkDestroyFramebuffer) X(vkCreateShaderModule) X(vkDestroyShaderModule)                           \
  X(vkCreatePipelineLayout) X(vkDestroyPipelineLayout) X(vkCreateGraphicsPipelines) X(vkDestroyPipeline)                    \
  X(vkCreateDescriptorSetLayout) X(vkDestroyDescriptorSetLayout) X(vkCreateDescriptorPool) X(vkDestroyDescriptorPool) \
  X(vkAllocateDescriptorSets) X(vkUpdateDescriptorSets) X(vkCmdBindDescriptorSets) \
  X(vkCmdBeginRenderPass) X(vkCmdEndRenderPass) X(vkCmdBindPipeline) X(vkCmdDraw)

#define DECLARE(name) static PFN_##name p_##name;
VK_FUNCTIONS(DECLARE)
VK_DEVICE_FUNCTIONS(DECLARE)
#undef DECLARE

static int vkStep(const char *name, VkResult result, const char *detail)
{
  if (result == VK_SUCCESS) probe_pass(); else probe_fail();
  probe_line("%s vk %s: VkResult %d%s%s", result == VK_SUCCESS ? "PASS" : "FAIL", name, (int)result,
             detail && *detail ? "; " : "", detail ? detail : "");
  return result == VK_SUCCESS;
}

static int vkCheck(const char *name, int ok, const char *detail)
{
  if (ok) probe_pass(); else probe_fail();
  probe_line("%s vk %s%s%s", ok ? "PASS" : "FAIL", name, detail && *detail ? ": " : "", detail ? detail : "");
  return ok;
}

static uint32_t memoryType(const VkPhysicalDeviceMemoryProperties *memory, uint32_t bits, VkMemoryPropertyFlags want)
{
  for (uint32_t i = 0; i < memory->memoryTypeCount; i++)
    if ((bits & (1u << i)) && (memory->memoryTypes[i].propertyFlags & want) == want) return i;
  return UINT32_MAX;
}

static void imageBarrierLevels(VkCommandBuffer cmd, VkImage image, VkImageLayout from, VkImageLayout to, VkAccessFlags srcAccess,
                         VkAccessFlags dstAccess, VkPipelineStageFlags srcStage, VkPipelineStageFlags dstStage, uint32_t levels)
{
  const VkImageMemoryBarrier barrier = {
      .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, .srcAccessMask = srcAccess, .dstAccessMask = dstAccess,
      .oldLayout = from, .newLayout = to, .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
      .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .image = image,
      .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, levels, 0, 1}};
  p_vkCmdPipelineBarrier(cmd, srcStage, dstStage, 0, 0, NULL, 0, NULL, 1, &barrier);
}

static void imageBarrier(VkCommandBuffer cmd, VkImage image, VkImageLayout from, VkImageLayout to, VkAccessFlags srcAccess,
                         VkAccessFlags dstAccess, VkPipelineStageFlags srcStage, VkPipelineStageFlags dstStage)
{
  imageBarrierLevels(cmd, image, from, to, srcAccess, dstAccess, srcStage, dstStage, 1);
}

// Eight rows of eight 8x8 cells; all 64 pixels of each cell are accounted for.
// Keep the diagnostic independent of the strict readback verdict below.
static void triangleCoverageGrid(const volatile uint8_t *pixels, const uint8_t foreground[4],
                                 const uint8_t background[4], int clearOnly)
{
  const char *kind = clearOnly ? "linear-clear-direct" : "triangle";
  if (clearOnly)
    probe_line("vk %s grid: columns x=0,8,...,56; cells C/B/O counts (sum 64): C=old clear B=expected background O=other", kind);
  else
    probe_line("vk triangle grid: columns x=0,8,...,56; cells C/T/B/O counts (sum 64): "
               "C=old clear T=expected triangle B=expected background O=other; diagonal accepts T or B");
  for (uint32_t cy = 0; cy < kHeight; cy += 8) {
    char row[128];
    size_t used = 0;
    for (uint32_t cx = 0; cx < kWidth; cx += 8) {
      unsigned counts[4] = {0};
      for (uint32_t y = cy; y < cy + 8; y++) {
        for (uint32_t x = cx; x < cx + 8; x++) {
          const uint32_t i = y * kWidth + x;
          int old = 1, tri = 1, bg = 1;
          for (uint32_t c = 0; c < 4; c++) {
            old &= pixels[4 * i + c] == kClear[c];
            tri &= pixels[4 * i + c] == foreground[c];
            bg &= pixels[4 * i + c] == background[c];
          }
          const int inside = !clearOnly && x >= 8 && y >= 8 && x + y < 63;
          const int edge = !clearOnly && x >= 8 && y >= 8 && x + y == 63;
          const unsigned category = old ? 0 : tri && (inside || edge) ? 1 : bg && (!inside || edge) ? 2 : 3;
          counts[category]++;
        }
      }
      const int n = clearOnly ? snprintf(row + used, sizeof(row) - used, "%s%02u/%02u/%02u",
                                          cx ? " " : "", counts[0], counts[2], counts[3]) :
                               snprintf(row + used, sizeof(row) - used, "%s%02u/%02u/%02u/%02u",
                                          cx ? " " : "", counts[0], counts[1], counts[2], counts[3]);
      if (n < 0 || (size_t)n >= sizeof(row) - used) return;
      used += (size_t)n;
    }
    probe_line("vk %s grid y=%02u..%02u: %s", kind, cy, cy + 7, row);
  }
}

// Shared strict coverage verdict for direct and copied pixels; neither result replaces the other.
static int triangleReadbackCheck(const volatile uint8_t *pixels, const char *mode)
{
  const uint8_t foreground[4] = {255, 0, 255, 255};
  const uint8_t backgroundColor[4] = {0x22, 0x44, 0x66, 0xff};
  char detail[256];
  uint32_t checked[2] = {0, 0}, matched[2] = {0, 0}, first = UINT32_MAX;
  // Pixel centers: (x+.5,y+.5). Only the diagonal x+y=63 lies exactly
  // on an edge; skip its 48 pixels to avoid dependence on edge tie-breaking.
  for (uint32_t y = 0; y < kHeight; y++) {
    for (uint32_t x = 0; x < kWidth; x++) {
      if (x >= 8 && y >= 8 && x + y == 63) continue;
      const int inside = x >= 8 && y >= 8 && x + y < 63;
      const uint8_t *expected = inside ? foreground : backgroundColor;
      const uint32_t i = y * kWidth + x;
      int same = 1;
      for (uint32_t c = 0; c < 4; c++) same &= pixels[4 * i + c] == expected[c];
      checked[inside]++;
      matched[inside] += same;
      if (!same && first == UINT32_MAX) first = i;
    }
  }
  if (first != UINT32_MAX) {
    const uint32_t x = first % kWidth, y = first / kWidth;
    const uint8_t *expected = x >= 8 && y >= 8 && x + y < 63 ? foreground : backgroundColor;
    probe_line("vk %s triangle mismatch (%u,%u): %02x%02x%02x%02x expected %02x%02x%02x%02x", mode, x, y,
        pixels[4 * first], pixels[4 * first + 1], pixels[4 * first + 2], pixels[4 * first + 3],
        expected[0], expected[1], expected[2], expected[3]);
  }
  snprintf(detail, sizeof detail, "%s %u/%u pixels = ff00ffff", mode, matched[1], checked[1]);
  const int insideOK = vkCheck("triangle interior readback", checked[1] != 0 && matched[1] == checked[1], detail);
  snprintf(detail, sizeof detail, "%s %u/%u pixels = 224466ff; 48 edge pixels excluded", mode, matched[0], checked[0]);
  const int backgroundOK = vkCheck("triangle background readback", checked[0] != 0 && matched[0] == checked[0], detail);
  return insideOK && backgroundOK;
}

// Vertex + fragment execution, separate from transfer-only clear/copy.
// Geometry has no vertex buffers or descriptors; direct readback does not use TQ.
struct TriangleDirectReadback { VkDeviceSize offset, rowPitch; int clearOnly; int completed; };

static int triangleProbeReadback(VkDevice device, VkQueue queue, VkCommandPool pool, VkImage image, VkBuffer buffer,
                                 VkDeviceMemory bufferMemory, void *mapped, int coherent,
                                 struct TriangleDirectReadback *direct)
{
  const int clearOnly = direct && direct->clearOnly;
  const char *mode = clearOnly ? "linear-clear-direct" : direct ? "linear-direct" : "optimal-copy";
  const char *kind = clearOnly ? "clear" : "triangle";
  probe_line("vk %s mode %s begin", kind, mode);
  VkImageView view = VK_NULL_HANDLE;
  VkRenderPass renderPass = VK_NULL_HANDLE;
  VkFramebuffer framebuffer = VK_NULL_HANDLE;
  VkShaderModule vertex = VK_NULL_HANDLE, fragment = VK_NULL_HANDLE;
  VkPipelineLayout layout = VK_NULL_HANDLE;
  VkPipeline pipeline = VK_NULL_HANDLE;
  VkFence fence = VK_NULL_HANDLE;
  int ok = 0;
  char detail[256];
  const uint8_t backgroundColor[4] = {0x22, 0x44, 0x66, 0xff};
#define TRI_STEP(name, expression) do { \
  snprintf(detail, sizeof detail, "%s %s", mode, name); \
  if (!vkStep(detail, (expression), NULL)) goto cleanup; \
} while (0)
  const VkImageViewCreateInfo viewInfo = {
      .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, .image = image, .viewType = VK_IMAGE_VIEW_TYPE_2D,
      .format = VK_FORMAT_R8G8B8A8_UNORM, .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
  TRI_STEP("vkCreateImageView", p_vkCreateImageView(device, &viewInfo, NULL, &view));
  const VkAttachmentDescription attachment = {
      .format = VK_FORMAT_R8G8B8A8_UNORM, .samples = VK_SAMPLE_COUNT_1_BIT,
      .loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR, .storeOp = VK_ATTACHMENT_STORE_OP_STORE,
      .stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE, .stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE,
      .initialLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL, .finalLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
  const VkAttachmentReference colorRef = {0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
  const VkSubpassDescription subpass = {.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS,
                                        .colorAttachmentCount = 1, .pColorAttachments = &colorRef};
  const VkRenderPassCreateInfo passInfo = {.sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO,
      .attachmentCount = 1, .pAttachments = &attachment, .subpassCount = 1, .pSubpasses = &subpass};
  TRI_STEP("vkCreateRenderPass", p_vkCreateRenderPass(device, &passInfo, NULL, &renderPass));
  const VkFramebufferCreateInfo framebufferInfo = {.sType = VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO,
      .renderPass = renderPass, .attachmentCount = 1, .pAttachments = &view, .width = kWidth, .height = kHeight, .layers = 1};
  TRI_STEP("vkCreateFramebuffer", p_vkCreateFramebuffer(device, &framebufferInfo, NULL, &framebuffer));
  if (!clearOnly) {
    VkShaderModuleCreateInfo shaderInfo = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = sizeof kTriangleVertSpv, .pCode = kTriangleVertSpv};
    TRI_STEP("vertex module", p_vkCreateShaderModule(device, &shaderInfo, NULL, &vertex));
    shaderInfo.codeSize = sizeof kTriangleFragSpv;
    shaderInfo.pCode = kTriangleFragSpv;
    TRI_STEP("fragment module", p_vkCreateShaderModule(device, &shaderInfo, NULL, &fragment));
    const VkPipelineLayoutCreateInfo layoutInfo = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    TRI_STEP("vkCreatePipelineLayout", p_vkCreatePipelineLayout(device, &layoutInfo, NULL, &layout));
    const VkPipelineShaderStageCreateInfo stages[] = {
        {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_VERTEX_BIT,
         .module = vertex, .pName = "main"},
        {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_FRAGMENT_BIT,
         .module = fragment, .pName = "main"}};
    const VkPipelineVertexInputStateCreateInfo input = {.sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO};
    const VkPipelineInputAssemblyStateCreateInfo assembly = {.sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO,
        .topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST};
    const VkViewport viewport = {0, 0, kWidth, kHeight, 0, 1};
    const VkRect2D scissor = {{0, 0}, {kWidth, kHeight}};
    const VkPipelineViewportStateCreateInfo viewportState = {.sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO,
        .viewportCount = 1, .pViewports = &viewport, .scissorCount = 1, .pScissors = &scissor};
    const VkPipelineRasterizationStateCreateInfo raster = {.sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO,
        .polygonMode = VK_POLYGON_MODE_FILL, .cullMode = VK_CULL_MODE_NONE, .frontFace = VK_FRONT_FACE_COUNTER_CLOCKWISE,
        .lineWidth = 1.0f};
    const VkPipelineMultisampleStateCreateInfo samples = {.sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO,
        .rasterizationSamples = VK_SAMPLE_COUNT_1_BIT};
    const VkPipelineColorBlendAttachmentState blendAttachment = {
        .colorWriteMask = VK_COLOR_COMPONENT_R_BIT | VK_COLOR_COMPONENT_G_BIT | VK_COLOR_COMPONENT_B_BIT | VK_COLOR_COMPONENT_A_BIT};
    const VkPipelineColorBlendStateCreateInfo blend = {.sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO,
        .attachmentCount = 1, .pAttachments = &blendAttachment};
    const VkGraphicsPipelineCreateInfo pipelineInfo = {.sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO,
        .stageCount = 2, .pStages = stages, .pVertexInputState = &input, .pInputAssemblyState = &assembly,
        .pViewportState = &viewportState, .pRasterizationState = &raster, .pMultisampleState = &samples,
        .pColorBlendState = &blend, .layout = layout, .renderPass = renderPass, .subpass = 0};
    TRI_STEP("vkCreateGraphicsPipelines", p_vkCreateGraphicsPipelines(device, VK_NULL_HANDLE, 1, &pipelineInfo, NULL, &pipeline));
  }
  VkCommandBuffer cmd = VK_NULL_HANDLE;
  const VkCommandBufferAllocateInfo cmdInfo = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
      .commandPool = pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
  TRI_STEP("vkAllocateCommandBuffers", p_vkAllocateCommandBuffers(device, &cmdInfo, &cmd));
  const VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
      .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
  TRI_STEP("vkBeginCommandBuffer", p_vkBeginCommandBuffer(cmd, &begin));
  imageBarrier(cmd, image, direct ? VK_IMAGE_LAYOUT_PREINITIALIZED : VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
               VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
               direct ? VK_ACCESS_HOST_WRITE_BIT : VK_ACCESS_TRANSFER_READ_BIT, VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,
               direct ? VK_PIPELINE_STAGE_HOST_BIT : VK_PIPELINE_STAGE_TRANSFER_BIT,
               VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT);
  if (!direct) {
    // Reuse the transfer readback buffer with an explicit write-after-write dependency.
    const VkBufferMemoryBarrier reuse = {.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = buffer, .offset = 0, .size = VK_WHOLE_SIZE};
    p_vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
                          0, 0, NULL, 1, &reuse, 0, NULL);
  }
  // A different background from the transfer clear also detects stale readback.
  const VkClearValue background = {.color = {.float32 = {
      backgroundColor[0] / 255.0f, backgroundColor[1] / 255.0f,
      backgroundColor[2] / 255.0f, backgroundColor[3] / 255.0f}}};
  const VkRenderPassBeginInfo renderBegin = {.sType = VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO,
      .renderPass = renderPass, .framebuffer = framebuffer, .renderArea = {{0, 0}, {kWidth, kHeight}},
      .clearValueCount = 1, .pClearValues = &background};
  p_vkCmdBeginRenderPass(cmd, &renderBegin, VK_SUBPASS_CONTENTS_INLINE);
  if (!clearOnly) {
    p_vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline);
    p_vkCmdDraw(cmd, 3, 1, 0, 0);
  }
  p_vkCmdEndRenderPass(cmd);
  if (direct) {
    imageBarrier(cmd, image, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL, VK_IMAGE_LAYOUT_GENERAL,
                 VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT, VK_ACCESS_HOST_READ_BIT,
                 VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, VK_PIPELINE_STAGE_HOST_BIT);
  } else {
    imageBarrier(cmd, image, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                 VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT,
                 VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
    const VkBufferImageCopy region = {.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
                                      .imageExtent = {kWidth, kHeight, 1}};
    p_vkCmdCopyImageToBuffer(cmd, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buffer, 1, &region);
    const VkBufferMemoryBarrier toHost = {.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = buffer, .offset = 0, .size = VK_WHOLE_SIZE};
    p_vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 0, NULL, 1, &toHost, 0, NULL);
  }
  TRI_STEP("vkEndCommandBuffer", p_vkEndCommandBuffer(cmd));
  const VkFenceCreateInfo fenceInfo = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
  TRI_STEP("vkCreateFence", p_vkCreateFence(device, &fenceInfo, NULL, &fence));
  const VkSubmitInfo submit = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cmd};
  TRI_STEP("vkQueueSubmit", p_vkQueueSubmit(queue, 1, &submit, fence));
  TRI_STEP("vkWaitForFences", p_vkWaitForFences(device, 1, &fence, VK_TRUE, 10000000000ull));
  if (direct) direct->completed = 1;
  if (direct || !coherent) {
    const VkMappedMemoryRange whole = {.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,
        .memory = bufferMemory, .offset = 0, .size = VK_WHOLE_SIZE};
    TRI_STEP("vkInvalidateMappedMemoryRanges", p_vkInvalidateMappedMemoryRanges(device, 1, &whole));
  }
  uint8_t packed[kWidth * kHeight * 4];
  if (direct) {
    const volatile uint8_t *source = mapped;
    for (uint32_t y = 0; y < kHeight; y++)
      for (uint32_t x = 0; x < kWidth * 4; x++)
        packed[y * kWidth * 4 + x] = source[direct->offset + y * direct->rowPitch + x];
  }
  const volatile uint8_t *pixels = direct ? packed : mapped;
  const uint8_t foreground[4] = {255, 0, 255, 255};
  triangleCoverageGrid(pixels, foreground, backgroundColor, clearOnly);
  if (clearOnly) {
    uint32_t matched = 0;
    for (uint32_t i = 0; i < kWidth * kHeight; i++) {
      int same = 1;
      for (uint32_t c = 0; c < 4; c++) same &= pixels[4 * i + c] == backgroundColor[c];
      matched += same;
    }
    snprintf(detail, sizeof detail, "%u/%u pixels = 224466ff; no Draw and no edge exclusions", matched, kWidth * kHeight);
    ok = vkCheck("linear-clear-direct readback", matched == kWidth * kHeight, detail);
    goto cleanup;
  }
  ok = triangleReadbackCheck(pixels, mode);
cleanup:
  // As in the transfer probe, ensure pending commands no longer use resources on error.
  p_vkDeviceWaitIdle(device);
  if (fence) p_vkDestroyFence(device, fence, NULL);
  if (pipeline) p_vkDestroyPipeline(device, pipeline, NULL);
  if (layout) p_vkDestroyPipelineLayout(device, layout, NULL);
  if (fragment) p_vkDestroyShaderModule(device, fragment, NULL);
  if (vertex) p_vkDestroyShaderModule(device, vertex, NULL);
  if (framebuffer) p_vkDestroyFramebuffer(device, framebuffer, NULL);
  if (renderPass) p_vkDestroyRenderPass(device, renderPass, NULL);
  if (view) p_vkDestroyImageView(device, view, NULL);
#undef TRI_STEP
  probe_line("vk %s mode %s end %s", kind, mode, ok ? "PASS" : "FAIL");
  return ok;
}

static int triangleProbe(VkDevice device, VkQueue queue, VkCommandPool pool, VkImage image, VkBuffer buffer,
                         VkDeviceMemory bufferMemory, void *mapped, int coherent)
{
  return triangleProbeReadback(device, queue, pool, image, buffer, bufferMemory, mapped, coherent, NULL);
}

// The source is GENERAL after the direct submission. No clear or draw is recorded here.
static int linearTriangleCopyReadback(VkDevice device, VkQueue queue, VkCommandPool pool, VkImage image,
                                      const VkPhysicalDeviceMemoryProperties *memory)
{
  VkBuffer buffer = VK_NULL_HANDLE;
  VkDeviceMemory allocation = VK_NULL_HANDLE;
  VkFence fence = VK_NULL_HANDLE;
  void *mapped = NULL;
  int ok = 0;
  probe_line("vk triangle mode linear-copy begin (same linear draw)");
#define COPY_STEP(name, expression) do { \
  if (!vkStep("linear-copy " name, (expression), NULL)) goto cleanup; \
} while (0)
  const VkBufferCreateInfo info = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
      .size = kWidth * kHeight * 4, .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT,
      .sharingMode = VK_SHARING_MODE_EXCLUSIVE};
  COPY_STEP("vkCreateBuffer", p_vkCreateBuffer(device, &info, NULL, &buffer));
  VkMemoryRequirements need;
  p_vkGetBufferMemoryRequirements(device, buffer, &need);
  uint32_t type = memoryType(memory, need.memoryTypeBits,
                             VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
  const int coherent = type != UINT32_MAX;
  if (!coherent) type = memoryType(memory, need.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT);
  if (!vkCheck("linear-copy host-visible memory", type != UINT32_MAX, NULL)) goto cleanup;
  const VkMemoryAllocateInfo allocate = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
      .allocationSize = need.size, .memoryTypeIndex = type};
  COPY_STEP("vkAllocateMemory", p_vkAllocateMemory(device, &allocate, NULL, &allocation));
  COPY_STEP("vkBindBufferMemory", p_vkBindBufferMemory(device, buffer, allocation, 0));
  COPY_STEP("vkMapMemory", p_vkMapMemory(device, allocation, 0, VK_WHOLE_SIZE, 0, &mapped));
  VkCommandBuffer cmd = VK_NULL_HANDLE;
  const VkCommandBufferAllocateInfo cmdInfo = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
      .commandPool = pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
  COPY_STEP("vkAllocateCommandBuffers", p_vkAllocateCommandBuffers(device, &cmdInfo, &cmd));
  const VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
      .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
  COPY_STEP("vkBeginCommandBuffer", p_vkBeginCommandBuffer(cmd, &begin));
  // Retain the original color-write dependency across submissions, after the CPU read.
  imageBarrier(cmd, image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
               VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT | VK_ACCESS_HOST_READ_BIT, VK_ACCESS_TRANSFER_READ_BIT,
               VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT | VK_PIPELINE_STAGE_HOST_BIT,
               VK_PIPELINE_STAGE_TRANSFER_BIT);
  const VkBufferImageCopy region = {.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
      .imageExtent = {kWidth, kHeight, 1}};
  p_vkCmdCopyImageToBuffer(cmd, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buffer, 1, &region);
  const VkBufferMemoryBarrier toHost = {.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
      .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
      .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
      .buffer = buffer, .offset = 0, .size = VK_WHOLE_SIZE};
  p_vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
                        0, 0, NULL, 1, &toHost, 0, NULL);
  COPY_STEP("vkEndCommandBuffer", p_vkEndCommandBuffer(cmd));
  const VkFenceCreateInfo fenceInfo = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
  COPY_STEP("vkCreateFence", p_vkCreateFence(device, &fenceInfo, NULL, &fence));
  const VkSubmitInfo submit = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
      .commandBufferCount = 1, .pCommandBuffers = &cmd};
  COPY_STEP("vkQueueSubmit", p_vkQueueSubmit(queue, 1, &submit, fence));
  COPY_STEP("vkWaitForFences", p_vkWaitForFences(device, 1, &fence, VK_TRUE, 10000000000ull));
  if (!coherent) {
    const VkMappedMemoryRange whole = {.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,
        .memory = allocation, .offset = 0, .size = VK_WHOLE_SIZE};
    COPY_STEP("vkInvalidateMappedMemoryRanges", p_vkInvalidateMappedMemoryRanges(device, 1, &whole));
  }
  ok = triangleReadbackCheck(mapped, "linear-copy");
cleanup:
  p_vkDeviceWaitIdle(device);
  if (fence) p_vkDestroyFence(device, fence, NULL);
  if (mapped) p_vkUnmapMemory(device, allocation);
  if (buffer) p_vkDestroyBuffer(device, buffer, NULL);
  if (allocation) p_vkFreeMemory(device, allocation, NULL);
#undef COPY_STEP
  probe_line("vk triangle mode linear-copy end %s", ok ? "PASS" : "FAIL");
  return ok;
}

// Direct readback is checked first; a separate copy then reads the same completed draw without rendering again.
static int linearTriangleProbe(VkDevice device, VkQueue queue, VkCommandPool pool,
                               const VkPhysicalDeviceMemoryProperties *memory, VkFormatFeatureFlags features, int clearOnly)
{
  const char *mode = clearOnly ? "linear-clear-direct" : "linear-direct";
  if (!(features & VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT)) {
    probe_line("vk mode %s SKIP: linear COLOR_ATTACHMENT unsupported (features 0x%x)", mode, features);
    return -1;
  }
  VkImage image = VK_NULL_HANDLE;
  VkDeviceMemory allocation = VK_NULL_HANDLE;
  void *mapped = NULL;
  int ok = 0;
  const VkImageCreateInfo info = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .imageType = VK_IMAGE_TYPE_2D,
      .format = VK_FORMAT_R8G8B8A8_UNORM, .extent = {kWidth, kHeight, 1}, .mipLevels = 1, .arrayLayers = 1,
      .samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_LINEAR, .usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT |
          (clearOnly ? 0 : VK_IMAGE_USAGE_TRANSFER_SRC_BIT),
      .sharingMode = VK_SHARING_MODE_EXCLUSIVE, .initialLayout = VK_IMAGE_LAYOUT_PREINITIALIZED};
  if (!vkStep("linear vkCreateImage", p_vkCreateImage(device, &info, NULL, &image), NULL)) goto cleanup;
  VkMemoryRequirements need;
  p_vkGetImageMemoryRequirements(device, image, &need);
  uint32_t type = memoryType(memory, need.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
  const int coherent = type != UINT32_MAX;
  if (!coherent) type = memoryType(memory, need.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT);
  if (type == UINT32_MAX) {
    probe_line("vk mode %s SKIP: no HOST_VISIBLE image memory", mode);
    ok = -1;
    goto cleanup;
  }
  const VkMemoryAllocateInfo allocate = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
      .allocationSize = need.size, .memoryTypeIndex = type};
  if (!vkStep("linear vkAllocateMemory", p_vkAllocateMemory(device, &allocate, NULL, &allocation), NULL) ||
      !vkStep("linear vkBindImageMemory", p_vkBindImageMemory(device, image, allocation, 0), NULL)) goto cleanup;
  const VkImageSubresource subresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0};
  VkSubresourceLayout layout;
  p_vkGetImageSubresourceLayout(device, image, &subresource, &layout);
  probe_line("vk %s layout: allocation %llu offset %llu size %llu rowPitch %llu type %u coherent %d",
             mode, (unsigned long long)need.size, (unsigned long long)layout.offset, (unsigned long long)layout.size,
             (unsigned long long)layout.rowPitch, type, coherent);
  // Bound every CPU access without overflow, including nonzero offsets and padded rows.
  const VkDeviceSize rowBytes = kWidth * 4;
  if (!vkCheck("linear subresource bounds", layout.offset <= need.size && layout.size <= need.size - layout.offset &&
               layout.size >= rowBytes && layout.rowPitch >= rowBytes &&
               layout.rowPitch <= (layout.size - rowBytes) / (kHeight - 1), NULL)) goto cleanup;
  if (!vkStep("linear vkMapMemory", p_vkMapMemory(device, allocation, 0, VK_WHOLE_SIZE, 0, &mapped), NULL)) goto cleanup;
  for (uint32_t y = 0; y < kHeight; y++)
    for (uint32_t x = 0; x < kWidth * 4; x++)
      ((volatile uint8_t *)mapped)[layout.offset + y * layout.rowPitch + x] = kClear[x % 4];
  // The entire allocation is mapped: offset zero and VK_WHOLE_SIZE satisfy nonCoherentAtomSize rules.
  const VkMappedMemoryRange whole = {.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,
      .memory = allocation, .offset = 0, .size = VK_WHOLE_SIZE};
  if (!vkStep("linear vkFlushMappedMemoryRanges", p_vkFlushMappedMemoryRanges(device, 1, &whole), NULL)) goto cleanup;
  struct TriangleDirectReadback direct = {layout.offset, layout.rowPitch, clearOnly, 0};
  ok = triangleProbeReadback(device, queue, pool, image, VK_NULL_HANDLE, allocation, mapped, coherent, &direct);
  if (!clearOnly && direct.completed) {
    // Always run after a completed draw, including a failed direct pixel verdict.
    const int copyOK = linearTriangleCopyReadback(device, queue, pool, image, memory);
    probe_line("vk same linear draw comparison: linear-direct %s; linear-copy %s (diagnostic, not a fix)",
               ok ? "PASS" : "FAIL", copyOK ? "PASS" : "FAIL");
    ok = ok && copyOK;
  }
cleanup:
  if (!ok) probe_line("vk mode %s setup/render FAIL", mode);
  if (mapped) p_vkUnmapMemory(device, allocation);
  if (image) p_vkDestroyImage(device, image, NULL);
  if (allocation) p_vkFreeMemory(device, allocation, NULL);
  return ok;
}

// Capability discovery does not add PASS steps to the existing probe. A missing
// optional extension is a SKIP; a failed enumeration remains a real failure.
static int hostImportSupported(VkPhysicalDevice physical)
{
  uint32_t count = 0;
  VkResult vr = p_vkEnumerateDeviceExtensionProperties(physical, NULL, &count, NULL);
  if (vr != VK_SUCCESS) { vkStep("host-import enumerate extensions count", vr, NULL); return -1; }
  if (!count) return 0;
  if (count > 1024) { vkCheck("host-import extension count bound", 0, NULL); return -1; }
  VkExtensionProperties *extensions = calloc(count, sizeof(*extensions));
  if (!extensions) { vkCheck("host-import extension list allocation", 0, NULL); return -1; }
  vr = p_vkEnumerateDeviceExtensionProperties(physical, NULL, &count, extensions);
  int supported = 0;
  if (vr != VK_SUCCESS) { vkStep("host-import enumerate extensions", vr, NULL); supported = -1; }
  else for (uint32_t i = 0; i < count; i++)
    if (!strcmp(extensions[i].extensionName, VK_EXT_EXTERNAL_MEMORY_HOST_EXTENSION_NAME)) supported = 1;
  free(extensions);
  return supported;
}

// Ordinary CPU allocation only: no GOP/device pointer. Import the complete
// buffer memory requirement (including any driver's guard), not just 16 KiB.
// This cannot establish whether an exact-size scanout allocation is importable.
static int hostImportProbe(VkPhysicalDevice physical, VkDevice device, VkQueue queue, uint32_t family,
                           const VkPhysicalDeviceMemoryProperties *memory, VkDeviceSize minAlignment)
{
  const VkDeviceSize bytes = (VkDeviceSize)kWidth * kHeight * 4;
  VkBuffer buffer = VK_NULL_HANDLE;
  VkImage image = VK_NULL_HANDLE;
  VkDeviceMemory imported = VK_NULL_HANDLE, imageMemory = VK_NULL_HANDLE;
  VkCommandPool pool = VK_NULL_HANDLE;
  VkFence fence = VK_NULL_HANDLE;
  void *pointer = NULL, *mapped = NULL;
  int ok = 0;
  char detail[256];
  probe_line("vk host-import begin: HOST_ALLOCATION, ordinary aligned CPU allocation; original tests completed");
#define HOST_STEP(name, expression) do { \
  if (!vkStep("host-import " name, (expression), NULL)) goto cleanup; \
} while (0)
  PFN_vkGetMemoryHostPointerPropertiesEXT getHostProperties =
      (PFN_vkGetMemoryHostPointerPropertiesEXT)p_vkGetDeviceProcAddr(device, "vkGetMemoryHostPointerPropertiesEXT");
  if (!vkCheck("host-import function", getHostProperties != NULL, NULL)) goto cleanup;
  const VkPhysicalDeviceExternalBufferInfo externalInfo = {
      .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_BUFFER_INFO, .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT,
      .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT};
  VkExternalBufferProperties external = {.sType = VK_STRUCTURE_TYPE_EXTERNAL_BUFFER_PROPERTIES};
  p_vkGetPhysicalDeviceExternalBufferProperties(physical, &externalInfo, &external);
  const VkExternalMemoryProperties *externalMemory = &external.externalMemoryProperties;
  if (!vkCheck("host-import buffer importable",
               (externalMemory->externalMemoryFeatures & VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT) &&
               (externalMemory->compatibleHandleTypes & VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT) &&
               !(externalMemory->externalMemoryFeatures & VK_EXTERNAL_MEMORY_FEATURE_DEDICATED_ONLY_BIT), NULL)) goto cleanup;
  const VkExternalMemoryBufferCreateInfo externalBuffer = {.sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO,
      .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT};
  const VkBufferCreateInfo bufferInfo = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .pNext = &externalBuffer,
      .size = bytes, .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT, .sharingMode = VK_SHARING_MODE_EXCLUSIVE};
  HOST_STEP("vkCreateBuffer", p_vkCreateBuffer(device, &bufferInfo, NULL, &buffer));
  VkMemoryRequirements need;
  p_vkGetBufferMemoryRequirements(device, buffer, &need);
  probe_line("vk host-import buffer: requested %llu need.size %llu alignment %llu memoryTypeBits %08x minImportedHostPointerAlignment %llu",
             (unsigned long long)bytes, (unsigned long long)need.size, (unsigned long long)need.alignment,
             need.memoryTypeBits, (unsigned long long)minAlignment);
  VkDeviceSize alignment = minAlignment;
  if (alignment < need.alignment) alignment = need.alignment;
  if (alignment < sizeof(void *)) alignment = sizeof(void *);
  if (!vkCheck("host-import allocation bounds", minAlignment && !(minAlignment & (minAlignment - 1)) &&
               need.alignment && !(need.alignment & (need.alignment - 1)) && need.size >= bytes &&
               alignment <= SIZE_MAX && need.size <= SIZE_MAX - (alignment - 1), NULL)) goto cleanup;
  const VkDeviceSize allocationSize = (need.size + alignment - 1) & ~(alignment - 1);
  if (!vkCheck("host-import bounded allocation", allocationSize <= 64u * 1024u * 1024u, NULL)) goto cleanup;
  const int allocationError = posix_memalign(&pointer, (size_t)alignment, (size_t)allocationSize);
  if (!vkCheck("host-import posix_memalign", allocationError == 0, NULL)) goto cleanup;
  probe_line("vk host-import backing: requested %llu required %llu imported %llu alignment %llu; full requirement backed, not an exact-request-size import",
             (unsigned long long)bytes, (unsigned long long)need.size, (unsigned long long)allocationSize,
             (unsigned long long)alignment);
  VkMemoryHostPointerPropertiesEXT host = {.sType = VK_STRUCTURE_TYPE_MEMORY_HOST_POINTER_PROPERTIES_EXT};
  HOST_STEP("vkGetMemoryHostPointerPropertiesEXT", getHostProperties(device,
      VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT, pointer, &host));
  const uint32_t compatibleBits = host.memoryTypeBits & need.memoryTypeBits;
  uint32_t type = memoryType(memory, compatibleBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
  const int coherent = type != UINT32_MAX;
  if (!coherent) type = memoryType(memory, compatibleBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT);
  snprintf(detail, sizeof detail, "host bits %08x buffer bits %08x intersection %08x type %u coherent %d",
           host.memoryTypeBits, need.memoryTypeBits, compatibleBits, type, coherent);
  if (!vkCheck("host-import memory type intersection", type != UINT32_MAX, detail)) goto cleanup;
  // Host pointer imports must not chain a dedicated buffer/image allocation
  // (VUID-VkMemoryAllocateInfo-pNext-02806).
  const VkImportMemoryHostPointerInfoEXT importInfo = {.sType = VK_STRUCTURE_TYPE_IMPORT_MEMORY_HOST_POINTER_INFO_EXT,
      .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT,
      .pHostPointer = pointer};
  const VkMemoryAllocateInfo allocate = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .pNext = &importInfo,
      .allocationSize = allocationSize, .memoryTypeIndex = type};
  HOST_STEP("vkAllocateMemory import", p_vkAllocateMemory(device, &allocate, NULL, &imported));
  HOST_STEP("vkBindBufferMemory", p_vkBindBufferMemory(device, buffer, imported, 0));
  HOST_STEP("vkMapMemory", p_vkMapMemory(device, imported, 0, VK_WHOLE_SIZE, 0, &mapped));
  // vkMapMemory may return an alias of pointer. Flush/invalidate the complete
  // mapped allocation at offset zero, satisfying nonCoherentAtomSize rules.
  memset(mapped, 0xcd, (size_t)allocationSize);
  const VkMappedMemoryRange whole = {.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,
      .memory = imported, .offset = 0, .size = VK_WHOLE_SIZE};
  HOST_STEP("vkFlushMappedMemoryRanges", p_vkFlushMappedMemoryRanges(device, 1, &whole));

  const VkImageCreateInfo imageInfo = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .imageType = VK_IMAGE_TYPE_2D,
      .format = VK_FORMAT_R8G8B8A8_UNORM, .extent = {kWidth, kHeight, 1}, .mipLevels = 1, .arrayLayers = 1,
      .samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_OPTIMAL,
      .usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
      .sharingMode = VK_SHARING_MODE_EXCLUSIVE, .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED};
  HOST_STEP("vkCreateImage", p_vkCreateImage(device, &imageInfo, NULL, &image));
  VkMemoryRequirements imageNeed;
  p_vkGetImageMemoryRequirements(device, image, &imageNeed);
  uint32_t imageType = memoryType(memory, imageNeed.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
  if (imageType == UINT32_MAX) imageType = memoryType(memory, imageNeed.memoryTypeBits, 0);
  if (!vkCheck("host-import image memory type", imageType != UINT32_MAX, NULL)) goto cleanup;
  const VkMemoryAllocateInfo imageAllocate = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
      .allocationSize = imageNeed.size, .memoryTypeIndex = imageType};
  HOST_STEP("vkAllocateMemory image", p_vkAllocateMemory(device, &imageAllocate, NULL, &imageMemory));
  HOST_STEP("vkBindImageMemory", p_vkBindImageMemory(device, image, imageMemory, 0));
  const VkCommandPoolCreateInfo poolInfo = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = family};
  HOST_STEP("vkCreateCommandPool", p_vkCreateCommandPool(device, &poolInfo, NULL, &pool));
  VkCommandBuffer cmd = VK_NULL_HANDLE;
  const VkCommandBufferAllocateInfo cmdInfo = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
      .commandPool = pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
  HOST_STEP("vkAllocateCommandBuffers", p_vkAllocateCommandBuffers(device, &cmdInfo, &cmd));
  const VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
      .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
  HOST_STEP("vkBeginCommandBuffer", p_vkBeginCommandBuffer(cmd, &begin));
  const VkBufferMemoryBarrier fromHost = {.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
      .srcAccessMask = VK_ACCESS_HOST_WRITE_BIT, .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
      .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
      .buffer = buffer, .offset = 0, .size = VK_WHOLE_SIZE};
  probe_line("vk hostclear begin");
  imageBarrier(cmd, image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
               0, VK_ACCESS_TRANSFER_WRITE_BIT, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
  const uint8_t expected[4] = {0x39, 0x72, 0xb4, 0xe1};
  const VkClearColorValue color = {.float32 = {expected[0] / 255.0f, expected[1] / 255.0f,
                                             expected[2] / 255.0f, expected[3] / 255.0f}};
  const VkImageSubresourceRange range = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
  p_vkCmdClearColorImage(cmd, image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &color, 1, &range);
  imageBarrier(cmd, image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
               VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
  HOST_STEP("hostclear vkEndCommandBuffer", p_vkEndCommandBuffer(cmd));
  const VkFenceCreateInfo fenceInfo = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
  HOST_STEP("hostclear vkCreateFence", p_vkCreateFence(device, &fenceInfo, NULL, &fence));
  const VkSubmitInfo submit = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cmd};
  HOST_STEP("hostclear vkQueueSubmit", p_vkQueueSubmit(queue, 1, &submit, fence));
  HOST_STEP("hostclear vkWaitForFences", p_vkWaitForFences(device, 1, &fence, VK_TRUE, 10000000000ull));
  probe_line("vk hostclear end PASS");
  p_vkDestroyFence(device, fence, NULL);
  fence = VK_NULL_HANDLE;

  // Keep the completed clear command buffer owned by the pool; copy gets a
  // fresh buffer and fence, so a clear failure cannot submit any copy work.
  probe_line("vk hostcopy begin");
  HOST_STEP("hostcopy vkAllocateCommandBuffers", p_vkAllocateCommandBuffers(device, &cmdInfo, &cmd));
  HOST_STEP("hostcopy vkBeginCommandBuffer", p_vkBeginCommandBuffer(cmd, &begin));
  p_vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_HOST_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 1, &fromHost, 0, NULL);
  const VkBufferImageCopy region = {.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
      .imageExtent = {kWidth, kHeight, 1}};   // tightly packed, offset zero: exactly 16384 bytes
  p_vkCmdCopyImageToBuffer(cmd, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buffer, 1, &region);
  const VkBufferMemoryBarrier toHost = {.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
      .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
      .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
      .buffer = buffer, .offset = 0, .size = VK_WHOLE_SIZE};
  p_vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 0, NULL, 1, &toHost, 0, NULL);
  HOST_STEP("hostcopy vkEndCommandBuffer", p_vkEndCommandBuffer(cmd));
  HOST_STEP("hostcopy vkCreateFence", p_vkCreateFence(device, &fenceInfo, NULL, &fence));
  HOST_STEP("hostcopy vkQueueSubmit", p_vkQueueSubmit(queue, 1, &submit, fence));
  HOST_STEP("hostcopy vkWaitForFences", p_vkWaitForFences(device, 1, &fence, VK_TRUE, 10000000000ull));
  probe_line("vk hostcopy end PASS");
  HOST_STEP("vkInvalidateMappedMemoryRanges", p_vkInvalidateMappedMemoryRanges(device, 1, &whole));
  // Flush/invalidate apply to vkMapMemory's pointer, not an arbitrary alias.
  // Darwin returns the original pointer; an arbitrary alias would require
  // platform-specific cache synchronization outside Vulkan's scope.
  const int checkOriginal = pointer == mapped;
  const volatile uint8_t *pixels = pointer, *mappedPixels = mapped;
  uint32_t matched = 0, mappedMatched = 0, first = UINT32_MAX;
  for (uint32_t i = 0; i < kWidth * kHeight; i++) {
    int same = 1, mappedSame = 1;
    for (uint32_t c = 0; c < 4; c++) {
      if (checkOriginal) same &= pixels[4 * i + c] == expected[c];
      mappedSame &= mappedPixels[4 * i + c] == expected[c];
    }
    if (checkOriginal) matched += same;
    mappedMatched += mappedSame;
    if (checkOriginal && !same && first == UINT32_MAX) first = i;
  }
  if (!checkOriginal) probe_line("vk host-import original alias not read: cache synchronization covers the vkMapMemory mapping only");
  snprintf(detail, sizeof detail, "mapped %u/4096 pixels = 3972b4e1; original alias %s matched %u/4096 first mismatch %u",
           mappedMatched, checkOriginal ? "checked" : "not checked", matched, first);
  ok = vkCheck("host-import full CPU readback", (!checkOriginal || matched == kWidth * kHeight) && mappedMatched == kWidth * kHeight, detail);
cleanup:
  if (pool) p_vkDeviceWaitIdle(device);   // retire submissions before releasing imported pages, including on errors
  if (fence) p_vkDestroyFence(device, fence, NULL);
  if (pool) p_vkDestroyCommandPool(device, pool, NULL);
  if (mapped) p_vkUnmapMemory(device, imported);
  if (buffer) p_vkDestroyBuffer(device, buffer, NULL);
  if (imported) p_vkFreeMemory(device, imported, NULL);
  free(pointer);   // host allocation outlives the imported VkDeviceMemory
  if (image) p_vkDestroyImage(device, image, NULL);
  if (imageMemory) p_vkFreeMemory(device, imageMemory, NULL);
  probe_line("vk host-import end %s", ok ? "PASS" : "FAIL");
#undef HOST_STEP
  return ok;
}

#include "powervrprobed_roa.h"

int probe_vulkan(const char *library)
{
  // Owner probe only: the Darwin winsys bounds its opt-in geometry trace to 32 lines.
  if (setenv("PVR_DARWIN_DEBUG_GEOMETRY", "1", 1) != 0) return -1;
  // Isolate terminal PPP clipping; do not combine this with the overlap comparison.
  if (unsetenv("PVR_DARWIN_VENDOR_ISP_OVERLAP") != 0) return -1;
  if (setenv("PVR_DARWIN_TERMINATE_TILE_UNITS", "1", 1) != 0) return -1;
  if (setenv("PVR_DARWIN_BUFFER_GUARD", "1", 1) != 0) return -1;
  const char *roaFlag=getenv("PVR_PROBE_ROA_COLOR");
  const int roa=roaFlag && strcmp(roaFlag,"1")==0;
  const char *uiBatchFlag = getenv("PVR_PROBE_UI_BATCH");
  const int uiBatch = uiBatchFlag && strcmp(uiBatchFlag, "1") == 0;
  const char *uiFillFlag = getenv("PVR_PROBE_UI_FILL");
  const int uiFill = uiBatch || (uiFillFlag && strcmp(uiFillFlag, "1") == 0);
  if(roa && uiFill){vkCheck("ROA exclusive mode",0,"unset UI_FILL/UI_BATCH for the independent overlap test");return -1;}
  const uint32_t width = uiFill ? 388U : kWidth, height = uiFill ? 12U : kHeight;
  const uint32_t mipLevels = uiBatch ? 4U : 1U;
  uint32_t pixelCount = 0;
  for (uint32_t level = 0; level < mipLevels; ++level) {
    const uint32_t w = (width >> level) ? width >> level : 1U;
    const uint32_t h = (height >> level) ? height >> level : 1U;
    pixelCount += w * h;
  }
  const VkFormat imageFormat = roa ? VK_FORMAT_R32_UINT : uiFill ? VK_FORMAT_B8G8R8A8_UNORM : VK_FORMAT_R8G8B8A8_UNORM;
  const uint8_t expected[4] = {uiFill ? kClear[2] : kClear[0], kClear[1], uiFill ? kClear[0] : kClear[2], kClear[3]};
  if (uiBatch) {
    probe_line("vk UI-batch comparison: optimal B8G8R8A8_UNORM mips 388x12/194x6/97x3/48x1, 6159 pixels; exact UI format not established");
    probe_line("vk UI-batch SKIP: original 64x64 render and host-import tests (not a PASS)");
  } else if (uiFill) {
    probe_line("vk UI-fill comparison: 388x12 optimal B8G8R8A8_UNORM; known-format comparison, exact UI format not established");
    probe_line("vk UI-fill SKIP: original 64x64 render and host-import tests (not a PASS)");
  }
  const char *tqFirstFlag = getenv("PVR_PROBE_TQ_FIRST");
  const int tqFirst = uiFill || (tqFirstFlag && strcmp(tqFirstFlag, "1") == 0);
  char detail[256];
  setenv("PVR_I_WANT_A_BROKEN_VULKAN_DRIVER", "1", 1);
  void *driver = dlopen(library, RTLD_NOW | RTLD_LOCAL);
  if (!vkCheck("dlopen", driver != NULL, driver ? library : dlerror())) return -1;
  PFN_vkGetInstanceProcAddr gipa = (PFN_vkGetInstanceProcAddr)dlsym(driver, "vkGetInstanceProcAddr");
  if (!gipa) gipa = (PFN_vkGetInstanceProcAddr)dlsym(driver, "vk_icdGetInstanceProcAddr");
  if (!vkCheck("vkGetInstanceProcAddr", gipa != NULL, NULL)) return -1;

  PFN_vkCreateInstance createInstance = (PFN_vkCreateInstance)gipa(NULL, "vkCreateInstance");
  if (!vkCheck("vkCreateInstance lookup", createInstance != NULL, NULL)) return -1;
  const VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "powervrprobed",
                                 .apiVersion = VK_API_VERSION_1_2}; // Core driver-properties query below; pinned Mesa supports 1.2.
  const VkInstanceCreateInfo instanceInfo = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app};
  VkInstance instance = VK_NULL_HANDLE;
  if (!vkStep("vkCreateInstance", createInstance(&instanceInfo, NULL, &instance), NULL)) return -1;
#define LOAD(name) p_##name = (PFN_##name)gipa(instance, #name);
  VK_FUNCTIONS(LOAD)
#undef LOAD
#define CHECK_LOADED(name) && p_##name
  if (!vkCheck("instance functions", 1 VK_FUNCTIONS(CHECK_LOADED), NULL)) return -1;
#undef CHECK_LOADED

  int result = -1;
  const char *transferStage = NULL;
  VkDevice device = VK_NULL_HANDLE;
  uint32_t count = 1;
  VkPhysicalDevice physical = VK_NULL_HANDLE;
  VkResult vr = p_vkEnumeratePhysicalDevices(instance, &count, &physical);
  snprintf(detail, sizeof detail, "%u device(s)", vr == VK_SUCCESS || vr == VK_INCOMPLETE ? count : 0);
  if (!(vr == VK_SUCCESS || vr == VK_INCOMPLETE) || !count) {
    vkStep("vkEnumeratePhysicalDevices", vr == VK_SUCCESS ? VK_ERROR_INITIALIZATION_FAILED : vr, detail);
    goto out_instance;
  }
  vkStep("vkEnumeratePhysicalDevices", VK_SUCCESS, detail);
  VkPhysicalDeviceProperties properties;
  p_vkGetPhysicalDeviceProperties(physical, &properties);
  probe_line("vk device: %s, api %u.%u.%u, driver 0x%x, vendor 0x%x device 0x%x", properties.deviceName,
             VK_API_VERSION_MAJOR(properties.apiVersion), VK_API_VERSION_MINOR(properties.apiVersion),
             VK_API_VERSION_PATCH(properties.apiVersion), properties.driverVersion, properties.vendorID, properties.deviceID);
  VkPhysicalDeviceDriverProperties driverProperties = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES};
  VkPhysicalDeviceProperties2 properties2 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,
                                             .pNext = &driverProperties};
  p_vkGetPhysicalDeviceProperties2(physical, &properties2);
  probe_line("vk driver: id %u name '%.*s' conformance %u.%u.%u.%u",
             (unsigned)driverProperties.driverID, (int)sizeof(driverProperties.driverName), driverProperties.driverName,
             (unsigned)driverProperties.conformanceVersion.major, (unsigned)driverProperties.conformanceVersion.minor,
             (unsigned)driverProperties.conformanceVersion.subminor, (unsigned)driverProperties.conformanceVersion.patch);
  probe_line("vk driverInfo: '%.*s'", (int)sizeof(driverProperties.driverInfo), driverProperties.driverInfo);
  const int hostImport = (uiFill || roa) ? 0 : hostImportSupported(physical);
  VkPhysicalDeviceExternalMemoryHostPropertiesEXT hostProperties = {
      .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_MEMORY_HOST_PROPERTIES_EXT};
  if (hostImport == 1) {
    VkPhysicalDeviceProperties2 hostProperties2 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,
        .pNext = &hostProperties};
    p_vkGetPhysicalDeviceProperties2(physical, &hostProperties2);
  }

  VkQueueFamilyProperties families[8];
  uint32_t familyCount = 8, family = UINT32_MAX;
  p_vkGetPhysicalDeviceQueueFamilyProperties(physical, &familyCount, families);
  for (uint32_t i = 0; i < familyCount && family == UINT32_MAX; i++)
    if (families[i].queueCount && (families[i].queueFlags & VK_QUEUE_GRAPHICS_BIT)) family = i;
  snprintf(detail, sizeof detail, "family %u of %u, flags 0x%x", family, familyCount, family < familyCount ? families[family].queueFlags : 0);
  if (!vkCheck("graphics queue family", family != UINT32_MAX, detail)) goto out_instance;

  VkFormatProperties format;
  p_vkGetPhysicalDeviceFormatProperties(physical, imageFormat, &format);
  snprintf(detail, sizeof detail, "optimal features 0x%x", format.optimalTilingFeatures);
  vkCheck(roa ? "ROA R32_UINT transfer" : uiFill ? "UI-fill B8G8R8A8_UNORM transfer" : "R8G8B8A8_UNORM transfer", (format.optimalTilingFeatures & (VK_FORMAT_FEATURE_TRANSFER_SRC_BIT |
                                      VK_FORMAT_FEATURE_TRANSFER_DST_BIT)) == (VK_FORMAT_FEATURE_TRANSFER_SRC_BIT |
                                      VK_FORMAT_FEATURE_TRANSFER_DST_BIT), detail);

  if (!vkCheck(roa ? "ROA R32_UINT color attachment" : uiFill ? "UI-fill B8G8R8A8_UNORM color attachment" : "R8G8B8A8_UNORM color attachment",
               (format.optimalTilingFeatures & VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT) != 0, detail)) goto out_instance;

  const float priority = 1.0f;
  const VkDeviceQueueCreateInfo queueInfo = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = family,
                                             .queueCount = 1, .pQueuePriorities = &priority};
  if(roa && !roaSupported(physical))goto out_instance;
  VkPhysicalDeviceRasterizationOrderAttachmentAccessFeaturesEXT roaEnable={
      .sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_RASTERIZATION_ORDER_ATTACHMENT_ACCESS_FEATURES_EXT,
      .rasterizationOrderColorAttachmentAccess=VK_TRUE};
  const char *extensions[]={roa?VK_EXT_RASTERIZATION_ORDER_ATTACHMENT_ACCESS_EXTENSION_NAME:VK_EXT_EXTERNAL_MEMORY_HOST_EXTENSION_NAME};
  const VkDeviceCreateInfo deviceInfo = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1,
      .pNext=roa?&roaEnable:NULL,.pQueueCreateInfos = &queueInfo, .enabledExtensionCount = roa || hostImport == 1 ? 1u : 0u,
      .ppEnabledExtensionNames = extensions};
  if (!vkStep("vkCreateDevice", p_vkCreateDevice(physical, &deviceInfo, NULL, &device), NULL)) goto out_instance;
#define LOAD(name) p_##name = (PFN_##name)p_vkGetDeviceProcAddr(device, #name);
  VK_DEVICE_FUNCTIONS(LOAD)
#undef LOAD
#define CHECK_LOADED(name) && p_##name
  if (!vkCheck("device functions", 1 VK_DEVICE_FUNCTIONS(CHECK_LOADED), NULL)) goto out_instance;
#undef CHECK_LOADED

  VkQueue queue;
  p_vkGetDeviceQueue(device, family, 0, &queue);
  VkPhysicalDeviceMemoryProperties memory;
  p_vkGetPhysicalDeviceMemoryProperties(physical, &memory);

  VkImage image = VK_NULL_HANDLE;
  VkBuffer buffer = VK_NULL_HANDLE;
  VkDeviceMemory imageMemory = VK_NULL_HANDLE, bufferMemory = VK_NULL_HANDLE;
  VkCommandPool pool = VK_NULL_HANDLE;
  VkFence fence = VK_NULL_HANDLE;
  void *mapped = NULL;
  const VkDeviceSize bytes = (VkDeviceSize)pixelCount * 4;

  const VkImageCreateInfo imageInfo = {
      .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .imageType = VK_IMAGE_TYPE_2D, .format = imageFormat,
      .extent = {width, height, 1}, .mipLevels = mipLevels, .arrayLayers = 1, .samples = VK_SAMPLE_COUNT_1_BIT,
      .tiling = VK_IMAGE_TILING_OPTIMAL, .usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | (roa ? VK_IMAGE_USAGE_INPUT_ATTACHMENT_BIT : 0),
      .sharingMode = VK_SHARING_MODE_EXCLUSIVE, .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED};
  if (!vkStep("vkCreateImage", p_vkCreateImage(device, &imageInfo, NULL, &image), NULL)) goto out_device;
  VkMemoryRequirements need;
  p_vkGetImageMemoryRequirements(device, image, &need);
  uint32_t type = memoryType(&memory, need.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
  if (type == UINT32_MAX) type = memoryType(&memory, need.memoryTypeBits, 0);
  VkMemoryAllocateInfo allocate = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = need.size, .memoryTypeIndex = type};
  snprintf(detail, sizeof detail, "image %llu bytes, type %u", (unsigned long long)need.size, type);
  if (!vkStep("vkAllocateMemory image", type == UINT32_MAX ? VK_ERROR_OUT_OF_DEVICE_MEMORY : p_vkAllocateMemory(device, &allocate, NULL, &imageMemory), detail) ||
      !vkStep("vkBindImageMemory", p_vkBindImageMemory(device, image, imageMemory, 0), NULL))
    goto out_objects;

  const VkBufferCreateInfo bufferInfo = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = bytes,
                                         .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT, .sharingMode = VK_SHARING_MODE_EXCLUSIVE};
  if (!vkStep("vkCreateBuffer", p_vkCreateBuffer(device, &bufferInfo, NULL, &buffer), NULL)) goto out_objects;
  p_vkGetBufferMemoryRequirements(device, buffer, &need);
  type = memoryType(&memory, need.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
  const int coherent = type != UINT32_MAX;
  if (!coherent) type = memoryType(&memory, need.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT);
  allocate.allocationSize = need.size;
  allocate.memoryTypeIndex = type;
  snprintf(detail, sizeof detail, "buffer %llu bytes, type %u%s", (unsigned long long)need.size, type, coherent ? " coherent" : "");
  if (!vkStep("vkAllocateMemory buffer", type == UINT32_MAX ? VK_ERROR_OUT_OF_DEVICE_MEMORY : p_vkAllocateMemory(device, &allocate, NULL, &bufferMemory), detail) ||
      !vkStep("vkBindBufferMemory", p_vkBindBufferMemory(device, buffer, bufferMemory, 0), NULL))
    goto out_objects;
  if (!vkStep("vkMapMemory", p_vkMapMemory(device, bufferMemory, 0, VK_WHOLE_SIZE, 0, &mapped), NULL)) goto out_objects;
  for (VkDeviceSize i = 0; i < bytes / 4; i++) ((volatile uint32_t *)mapped)[i] = 0xdeadbeefu;
  if (!coherent) {
    // A host write to non-coherent memory needs a flush; the buffer is only a canary here, the readback below invalidates.
    probe_line("vk buffer memory is not host-coherent: the prefill may not be visible to the GPU (canary only)");
  }

  const VkCommandPoolCreateInfo poolInfo = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = family};
  VkCommandBuffer cmd = VK_NULL_HANDLE;
  const VkCommandBufferAllocateInfo cmdInfo = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandBufferCount = 1,
                                               .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY};
  if (!vkStep("vkCreateCommandPool", p_vkCreateCommandPool(device, &poolInfo, NULL, &pool), NULL)) goto out_objects;
  if(roa){
    probe_line("ROA prototype: R32_UINT same attachment input/color; 128 instances in ONE draw; no SG setting changes");
    result=roaProbe(device,queue,pool,image,buffer,bufferMemory,mapped,coherent)?0:-1;
    goto out_objects;
  }
  // Default order preserves the independent render evidence before TQ. The
  // owner-only comparison defers these same checks until TQ readback succeeds.
  int linearClearOK = 0, linearOK = 0;
  if (tqFirst) {
    probe_line("vk order: TQ clear/copy first; render checks deferred (diagnostic, not a fix)");
  } else {
    linearClearOK = linearTriangleProbe(device, queue, pool, &memory, format.linearTilingFeatures, 1);
    linearOK = linearTriangleProbe(device, queue, pool, &memory, format.linearTilingFeatures, 0);
  }
  VkCommandBufferAllocateInfo cmdAlloc = cmdInfo;
  cmdAlloc.commandPool = pool;
  const VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
                                          .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
  transferStage = "clear";
  probe_line("vk initial transfer %s begin", transferStage);
  if (!vkStep("clear vkAllocateCommandBuffers", p_vkAllocateCommandBuffers(device, &cmdAlloc, &cmd), NULL) ||
      !vkStep("clear vkBeginCommandBuffer", p_vkBeginCommandBuffer(cmd, &begin), NULL))
    goto out_objects;
  imageBarrierLevels(cmd, image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0, VK_ACCESS_TRANSFER_WRITE_BIT,
               VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, mipLevels);
  const VkClearColorValue color = {.float32 = {kClear[0] / 255.0f, kClear[1] / 255.0f, kClear[2] / 255.0f, kClear[3] / 255.0f}};
  const VkImageSubresourceRange range = {VK_IMAGE_ASPECT_COLOR_BIT, 0, mipLevels, 0, 1};
  p_vkCmdClearColorImage(cmd, image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &color, 1, &range);
  if (!vkStep("clear vkEndCommandBuffer", p_vkEndCommandBuffer(cmd), NULL)) goto out_objects;
  const VkFenceCreateInfo fenceInfo = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
  if (!vkStep("clear vkCreateFence", p_vkCreateFence(device, &fenceInfo, NULL, &fence), NULL)) goto out_objects;
  // Distinct submissions and fences identify which TQ operation first fails.
  // submit references cmd, which is replaced with a fresh command buffer for copy below.
  const VkSubmitInfo submit = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cmd};
  if (!vkStep("clear vkQueueSubmit", p_vkQueueSubmit(queue, 1, &submit, fence), NULL) ||
      !vkStep("clear vkWaitForFences", p_vkWaitForFences(device, 1, &fence, VK_TRUE, 10000000000ull), NULL)) goto out_objects;
  probe_line("vk initial transfer clear end PASS (fence completed; pixels checked after copy)");
  transferStage = NULL;
  p_vkDestroyFence(device, fence, NULL);
  fence = VK_NULL_HANDLE;

  transferStage = "copy";
  probe_line("vk initial transfer %s begin", transferStage);
  if (!vkStep("copy vkAllocateCommandBuffers", p_vkAllocateCommandBuffers(device, &cmdAlloc, &cmd), NULL) ||
      !vkStep("copy vkBeginCommandBuffer", p_vkBeginCommandBuffer(cmd, &begin), NULL)) goto out_objects;
  // The previous submission left the image in TRANSFER_DST; retain the write-to-read dependency across submissions.
  imageBarrierLevels(cmd, image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_ACCESS_TRANSFER_WRITE_BIT,
               VK_ACCESS_TRANSFER_READ_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, mipLevels);
  VkBufferImageCopy regions[4] = {0};
  VkDeviceSize bufferOffset = 0;
  for (uint32_t level = 0; level < mipLevels; ++level) {
    const uint32_t w = (width >> level) ? width >> level : 1U;
    const uint32_t h = (height >> level) ? height >> level : 1U;
    regions[level] = (VkBufferImageCopy){.bufferOffset = bufferOffset,
        .imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, level, 0, 1}, .imageExtent = {w, h, 1}};
    bufferOffset += (VkDeviceSize)w * h * 4;
  }
  p_vkCmdCopyImageToBuffer(cmd, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buffer, mipLevels, regions);
  const VkBufferMemoryBarrier toHost = {.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER, .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
                                        .dstAccessMask = VK_ACCESS_HOST_READ_BIT, .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                                        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .buffer = buffer, .offset = 0,
                                        .size = VK_WHOLE_SIZE};
  p_vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 0, NULL, 1, &toHost, 0, NULL);
  if (!vkStep("copy vkEndCommandBuffer", p_vkEndCommandBuffer(cmd), NULL)) goto out_objects;
  if (!vkStep("copy vkCreateFence", p_vkCreateFence(device, &fenceInfo, NULL, &fence), NULL)) goto out_objects;
  if (!vkStep("copy vkQueueSubmit", p_vkQueueSubmit(queue, 1, &submit, fence), NULL)) goto out_objects;
  // Each operation has its own 10 s bound; no arbitrary delay between stages.
  if (!vkStep("copy vkWaitForFences", p_vkWaitForFences(device, 1, &fence, VK_TRUE, 10000000000ull), NULL)) goto out_objects;
  probe_line("vk initial transfer copy end PASS (fence completed)");
  transferStage = NULL;

  if (!coherent) {
    const VkMappedMemoryRange whole = {.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE, .memory = bufferMemory, .offset = 0,
                                       .size = VK_WHOLE_SIZE};
    if (!vkStep("vkInvalidateMappedMemoryRanges", p_vkInvalidateMappedMemoryRanges(device, 1, &whole), NULL)) goto out_objects;
  }
  const volatile uint8_t *pixels = (const volatile uint8_t *)mapped;
  uint32_t match = 0, first = UINT32_MAX;
  for (uint32_t i = 0; i < pixelCount; i++) {
    const int same = pixels[i * 4] == expected[0] && pixels[i * 4 + 1] == expected[1] && pixels[i * 4 + 2] == expected[2] &&
                     pixels[i * 4 + 3] == expected[3];
    match += same;
    if (!same && first == UINT32_MAX) first = i;
  }
  if (first == UINT32_MAX)
    snprintf(detail, sizeof detail, "%u/%u pixels = %02x%02x%02x%02x", match, pixelCount, expected[0], expected[1], expected[2], expected[3]);
  else
    snprintf(detail, sizeof detail, "%u/%u pixels match; first mismatch at %u: %02x%02x%02x%02x (expected %02x%02x%02x%02x)", match,
             pixelCount, first, pixels[first * 4], pixels[first * 4 + 1], pixels[first * 4 + 2], pixels[first * 4 + 3],
             expected[0], expected[1], expected[2], expected[3]);
  const int clearOK = vkCheck("clear readback", match == pixelCount, detail);
  if (uiFill) {
    result = clearOK ? 0 : -1;
    goto out_objects;
  }
  if (tqFirst) {
    if (!clearOK) {
      probe_line("vk TQ-first readback failed; no render tests on this context");
      goto out_objects;
    }
    linearClearOK = linearTriangleProbe(device, queue, pool, &memory, format.linearTilingFeatures, 1);
    linearOK = linearTriangleProbe(device, queue, pool, &memory, format.linearTilingFeatures, 0);
  }
  const int triangleOK = triangleProbe(device, queue, pool, image, buffer, bufferMemory, mapped, coherent);
  if (clearOK && triangleOK && linearOK != 0 && linearClearOK != 0) {
    result = 0;
    // Last: a host-import failure must not prevent any original render/TQ test.
    if (hostImport == 1) {
      if (!hostImportProbe(physical, device, queue, family, &memory, hostProperties.minImportedHostPointerAlignment)) result = -1;
    } else if (hostImport == 0) {
      probe_line("vk host-import SKIP: VK_EXT_external_memory_host absent (not a PASS)");
    } else {
      probe_line("vk host-import not run: extension discovery failed");
      result = -1;
    }
  } else {
    probe_line("vk host-import SKIP: original render/TQ checks failed (not a PASS)");
  }

out_objects:
  if (transferStage) probe_line("vk initial transfer %s end FAIL", transferStage);
  if (device) p_vkDeviceWaitIdle(device);
  if (fence) p_vkDestroyFence(device, fence, NULL);
  if (pool) p_vkDestroyCommandPool(device, pool, NULL);
  if (mapped) p_vkUnmapMemory(device, bufferMemory);
  if (buffer) p_vkDestroyBuffer(device, buffer, NULL);
  if (bufferMemory) p_vkFreeMemory(device, bufferMemory, NULL);
  if (image) p_vkDestroyImage(device, image, NULL);
  if (imageMemory) p_vkFreeMemory(device, imageMemory, NULL);
out_device:
  p_vkDestroyDevice(device, NULL);
out_instance:
  p_vkDestroyInstance(instance, NULL);
  probe_line("vulkan stage %s", result ? "failed" : "passed");
  return result;
}

