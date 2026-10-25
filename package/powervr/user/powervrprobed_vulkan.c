// SPDX-License-Identifier: MIT
// powervrprobed, the Mesa stage (built with POWERVRPROBED_VULKAN=1 by scripts/build-a04br3-powervr-probe.py --vulkan):
// the PowerVR Vulkan driver of scripts/build-pvr-darwin.py (libvulkan_powervr_mesa.dylib with the darwin winsys of
// patches/mesa/pvr-darwin-winsys.patch) clears an image, then draws a triangle and reads both back.
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
  X(vkCreateDevice) X(vkGetDeviceProcAddr)
#define VK_DEVICE_FUNCTIONS(X)                                                                                                \
  X(vkDestroyDevice) X(vkGetDeviceQueue) X(vkCreateImage) X(vkDestroyImage) X(vkGetImageMemoryRequirements)                  \
  X(vkCreateBuffer) X(vkDestroyBuffer) X(vkGetBufferMemoryRequirements) X(vkAllocateMemory) X(vkFreeMemory)                 \
  X(vkBindImageMemory) X(vkBindBufferMemory) X(vkMapMemory) X(vkUnmapMemory) X(vkInvalidateMappedMemoryRanges)                \
  X(vkCreateCommandPool) X(vkDestroyCommandPool) X(vkAllocateCommandBuffers) X(vkBeginCommandBuffer) X(vkEndCommandBuffer)   \
  X(vkCmdPipelineBarrier) X(vkCmdClearColorImage) X(vkCmdCopyImageToBuffer) X(vkCreateFence) X(vkDestroyFence)              \
  X(vkQueueSubmit) X(vkWaitForFences) X(vkDeviceWaitIdle)                                                            \
  X(vkCreateImageView) X(vkDestroyImageView) X(vkCreateRenderPass) X(vkDestroyRenderPass)                                   \
  X(vkCreateFramebuffer) X(vkDestroyFramebuffer) X(vkCreateShaderModule) X(vkDestroyShaderModule)                           \
  X(vkCreatePipelineLayout) X(vkDestroyPipelineLayout) X(vkCreateGraphicsPipelines) X(vkDestroyPipeline)                    \
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

static void imageBarrier(VkCommandBuffer cmd, VkImage image, VkImageLayout from, VkImageLayout to, VkAccessFlags srcAccess,
                         VkAccessFlags dstAccess, VkPipelineStageFlags srcStage, VkPipelineStageFlags dstStage)
{
  const VkImageMemoryBarrier barrier = {
      .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, .srcAccessMask = srcAccess, .dstAccessMask = dstAccess,
      .oldLayout = from, .newLayout = to, .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
      .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .image = image,
      .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
  p_vkCmdPipelineBarrier(cmd, srcStage, dstStage, 0, 0, NULL, 0, NULL, 1, &barrier);
}

// Eight rows of eight 8x8 cells; all 64 pixels of each cell are accounted for.
// Keep the diagnostic independent of the strict readback verdict below.
static void triangleCoverageGrid(const volatile uint8_t *pixels, const uint8_t foreground[4],
                                 const uint8_t background[4])
{
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
          const int inside = x >= 8 && y >= 8 && x + y < 63;
          const int edge = x >= 8 && y >= 8 && x + y == 63;
          const unsigned category = old ? 0 : tri && (inside || edge) ? 1 : bg && (!inside || edge) ? 2 : 3;
          counts[category]++;
        }
      }
      const int n = snprintf(row + used, sizeof(row) - used, "%s%02u/%02u/%02u/%02u",
                              cx ? " " : "", counts[0], counts[1], counts[2], counts[3]);
      if (n < 0 || (size_t)n >= sizeof(row) - used) return;
      used += (size_t)n;
    }
    probe_line("vk triangle grid y=%02u..%02u: %s", cy, cy + 7, row);
  }
}

// Vertex + fragment execution, separate from transfer-only clear/copy. Call only after
// the first submission has completed. Geometry has no vertex buffers or descriptors.
static int triangleProbe(VkDevice device, VkQueue queue, VkCommandPool pool, VkImage image, VkBuffer buffer,
                         VkDeviceMemory bufferMemory, void *mapped, int coherent)
{
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
#define TRI_STEP(name, expression) do { if (!vkStep("triangle " name, (expression), NULL)) goto cleanup; } while (0)
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
  VkCommandBuffer cmd = VK_NULL_HANDLE;
  const VkCommandBufferAllocateInfo cmdInfo = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
      .commandPool = pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
  TRI_STEP("vkAllocateCommandBuffers", p_vkAllocateCommandBuffers(device, &cmdInfo, &cmd));
  const VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
      .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
  TRI_STEP("vkBeginCommandBuffer", p_vkBeginCommandBuffer(cmd, &begin));
  imageBarrier(cmd, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
               VK_ACCESS_TRANSFER_READ_BIT, VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,
               VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT);
  // Reuse the transfer readback buffer with an explicit write-after-write dependency.
  const VkBufferMemoryBarrier reuse = {.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
      .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
      .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
      .buffer = buffer, .offset = 0, .size = VK_WHOLE_SIZE};
  p_vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
                        0, 0, NULL, 1, &reuse, 0, NULL);
  // A different background from the transfer clear also detects stale readback.
  const VkClearValue background = {.color = {.float32 = {
      backgroundColor[0] / 255.0f, backgroundColor[1] / 255.0f,
      backgroundColor[2] / 255.0f, backgroundColor[3] / 255.0f}}};
  const VkRenderPassBeginInfo renderBegin = {.sType = VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO,
      .renderPass = renderPass, .framebuffer = framebuffer, .renderArea = {{0, 0}, {kWidth, kHeight}},
      .clearValueCount = 1, .pClearValues = &background};
  p_vkCmdBeginRenderPass(cmd, &renderBegin, VK_SUBPASS_CONTENTS_INLINE);
  p_vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline);
  p_vkCmdDraw(cmd, 3, 1, 0, 0);
  p_vkCmdEndRenderPass(cmd);
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
  TRI_STEP("vkEndCommandBuffer", p_vkEndCommandBuffer(cmd));
  const VkFenceCreateInfo fenceInfo = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
  TRI_STEP("vkCreateFence", p_vkCreateFence(device, &fenceInfo, NULL, &fence));
  const VkSubmitInfo submit = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cmd};
  TRI_STEP("vkQueueSubmit", p_vkQueueSubmit(queue, 1, &submit, fence));
  TRI_STEP("vkWaitForFences", p_vkWaitForFences(device, 1, &fence, VK_TRUE, 10000000000ull));
  if (!coherent) {
    const VkMappedMemoryRange whole = {.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,
        .memory = bufferMemory, .offset = 0, .size = VK_WHOLE_SIZE};
    TRI_STEP("vkInvalidateMappedMemoryRanges", p_vkInvalidateMappedMemoryRanges(device, 1, &whole));
  }
  const volatile uint8_t *pixels = mapped;
  const uint8_t foreground[4] = {255, 0, 255, 255};
  triangleCoverageGrid(pixels, foreground, backgroundColor);
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
    probe_line("vk triangle mismatch (%u,%u): %02x%02x%02x%02x expected %02x%02x%02x%02x", x, y,
        pixels[4 * first], pixels[4 * first + 1], pixels[4 * first + 2], pixels[4 * first + 3],
        expected[0], expected[1], expected[2], expected[3]);
  }
  snprintf(detail, sizeof detail, "%u/%u pixels = ff00ffff", matched[1], checked[1]);
  const int insideOK = vkCheck("triangle interior readback", checked[1] != 0 && matched[1] == checked[1], detail);
  snprintf(detail, sizeof detail, "%u/%u pixels = 224466ff; 48 edge pixels excluded", matched[0], checked[0]);
  const int backgroundOK = vkCheck("triangle background readback", checked[0] != 0 && matched[0] == checked[0], detail);
  ok = insideOK && backgroundOK;
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
  return ok;
}

int probe_vulkan(const char *library)
{
  // Owner probe only: the Darwin winsys bounds its opt-in geometry trace to 32 lines.
  if (setenv("PVR_DARWIN_DEBUG_GEOMETRY", "1", 1) != 0) return -1;
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

  VkQueueFamilyProperties families[8];
  uint32_t familyCount = 8, family = UINT32_MAX;
  p_vkGetPhysicalDeviceQueueFamilyProperties(physical, &familyCount, families);
  for (uint32_t i = 0; i < familyCount && family == UINT32_MAX; i++)
    if (families[i].queueCount && (families[i].queueFlags & VK_QUEUE_GRAPHICS_BIT)) family = i;
  snprintf(detail, sizeof detail, "family %u of %u, flags 0x%x", family, familyCount, family < familyCount ? families[family].queueFlags : 0);
  if (!vkCheck("graphics queue family", family != UINT32_MAX, detail)) goto out_instance;

  VkFormatProperties format;
  p_vkGetPhysicalDeviceFormatProperties(physical, VK_FORMAT_R8G8B8A8_UNORM, &format);
  snprintf(detail, sizeof detail, "optimal features 0x%x", format.optimalTilingFeatures);
  vkCheck("R8G8B8A8_UNORM transfer", (format.optimalTilingFeatures & (VK_FORMAT_FEATURE_TRANSFER_SRC_BIT |
                                      VK_FORMAT_FEATURE_TRANSFER_DST_BIT)) == (VK_FORMAT_FEATURE_TRANSFER_SRC_BIT |
                                      VK_FORMAT_FEATURE_TRANSFER_DST_BIT), detail);

  if (!vkCheck("R8G8B8A8_UNORM color attachment",
               (format.optimalTilingFeatures & VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT) != 0, detail)) goto out_instance;

  const float priority = 1.0f;
  const VkDeviceQueueCreateInfo queueInfo = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = family,
                                             .queueCount = 1, .pQueuePriorities = &priority};
  const VkDeviceCreateInfo deviceInfo = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1,
                                         .pQueueCreateInfos = &queueInfo};
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
  const VkDeviceSize bytes = (VkDeviceSize)kWidth * kHeight * 4;

  const VkImageCreateInfo imageInfo = {
      .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .imageType = VK_IMAGE_TYPE_2D, .format = VK_FORMAT_R8G8B8A8_UNORM,
      .extent = {kWidth, kHeight, 1}, .mipLevels = 1, .arrayLayers = 1, .samples = VK_SAMPLE_COUNT_1_BIT,
      .tiling = VK_IMAGE_TILING_OPTIMAL, .usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT,
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
  VkCommandBufferAllocateInfo cmdAlloc = cmdInfo;
  cmdAlloc.commandPool = pool;
  const VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
                                          .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
  if (!vkStep("vkAllocateCommandBuffers", p_vkAllocateCommandBuffers(device, &cmdAlloc, &cmd), NULL) ||
      !vkStep("vkBeginCommandBuffer", p_vkBeginCommandBuffer(cmd, &begin), NULL))
    goto out_objects;
  imageBarrier(cmd, image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0, VK_ACCESS_TRANSFER_WRITE_BIT,
               VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
  const VkClearColorValue color = {.float32 = {kClear[0] / 255.0f, kClear[1] / 255.0f, kClear[2] / 255.0f, kClear[3] / 255.0f}};
  const VkImageSubresourceRange range = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
  p_vkCmdClearColorImage(cmd, image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &color, 1, &range);
  imageBarrier(cmd, image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_ACCESS_TRANSFER_WRITE_BIT,
               VK_ACCESS_TRANSFER_READ_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
  const VkBufferImageCopy region = {.bufferOffset = 0, .bufferRowLength = 0, .bufferImageHeight = 0,
                                    .imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, .imageOffset = {0, 0, 0},
                                    .imageExtent = {kWidth, kHeight, 1}};
  p_vkCmdCopyImageToBuffer(cmd, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buffer, 1, &region);
  const VkBufferMemoryBarrier toHost = {.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER, .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
                                        .dstAccessMask = VK_ACCESS_HOST_READ_BIT, .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                                        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .buffer = buffer, .offset = 0,
                                        .size = VK_WHOLE_SIZE};
  p_vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 0, NULL, 1, &toHost, 0, NULL);
  if (!vkStep("vkEndCommandBuffer", p_vkEndCommandBuffer(cmd), NULL)) goto out_objects;

  const VkFenceCreateInfo fenceInfo = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
  if (!vkStep("vkCreateFence", p_vkCreateFence(device, &fenceInfo, NULL, &fence), NULL)) goto out_objects;
  const VkSubmitInfo submit = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cmd};
  if (!vkStep("vkQueueSubmit", p_vkQueueSubmit(queue, 1, &submit, fence), NULL)) goto out_objects;
  // 10 s: a clear and a copy of 16 KiB; a firmware hang shows as VK_TIMEOUT (2) or VK_ERROR_DEVICE_LOST (-4).
  if (!vkStep("vkWaitForFences", p_vkWaitForFences(device, 1, &fence, VK_TRUE, 10000000000ull), NULL)) goto out_objects;

  if (!coherent) {
    const VkMappedMemoryRange whole = {.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE, .memory = bufferMemory, .offset = 0,
                                       .size = VK_WHOLE_SIZE};
    if (!vkStep("vkInvalidateMappedMemoryRanges", p_vkInvalidateMappedMemoryRanges(device, 1, &whole), NULL)) goto out_objects;
  }
  const volatile uint8_t *pixels = (const volatile uint8_t *)mapped;
  uint32_t match = 0, first = UINT32_MAX;
  for (uint32_t i = 0; i < kWidth * kHeight; i++) {
    const int same = pixels[i * 4] == kClear[0] && pixels[i * 4 + 1] == kClear[1] && pixels[i * 4 + 2] == kClear[2] &&
                     pixels[i * 4 + 3] == kClear[3];
    match += same;
    if (!same && first == UINT32_MAX) first = i;
  }
  if (first == UINT32_MAX)
    snprintf(detail, sizeof detail, "%u/%u pixels = %02x%02x%02x%02x", match, kWidth * kHeight, kClear[0], kClear[1], kClear[2], kClear[3]);
  else
    snprintf(detail, sizeof detail, "%u/%u pixels match; first mismatch at %u: %02x%02x%02x%02x (expected %02x%02x%02x%02x)", match,
             kWidth * kHeight, first, pixels[first * 4], pixels[first * 4 + 1], pixels[first * 4 + 2], pixels[first * 4 + 3],
             kClear[0], kClear[1], kClear[2], kClear[3]);
  const int clearOK = vkCheck("clear readback", match == kWidth * kHeight, detail);
  const int triangleOK = triangleProbe(device, queue, pool, image, buffer, bufferMemory, mapped, coherent);
  if (clearOK && triangleOK) result = 0;

out_objects:
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

