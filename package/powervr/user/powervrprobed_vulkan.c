// SPDX-License-Identifier: MIT
// powervrprobed, the Mesa stage (built with POWERVRPROBED_VULKAN=1 by scripts/build-a04br3-powervr-probe.py --vulkan):
// the PowerVR Vulkan driver of scripts/build-pvr-darwin.py (libvulkan_powervr_mesa.dylib with the darwin winsys of
// patches/mesa/pvr-darwin-winsys.patch) clears an image on the GPU and the probe reads it back.
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

enum { kWidth = 64, kHeight = 64 };
// Exactly representable in UNORM8: k / 255 converts back to k.
static const uint8_t kClear[4] = {0x11, 0x5a, 0xa5, 0xee};

#define VK_FUNCTIONS(X)                                                                                                       \
  X(vkDestroyInstance) X(vkEnumeratePhysicalDevices) X(vkGetPhysicalDeviceProperties)                                       \
  X(vkGetPhysicalDeviceQueueFamilyProperties) X(vkGetPhysicalDeviceMemoryProperties) X(vkGetPhysicalDeviceFormatProperties) \
  X(vkCreateDevice) X(vkGetDeviceProcAddr)
#define VK_DEVICE_FUNCTIONS(X)                                                                                                \
  X(vkDestroyDevice) X(vkGetDeviceQueue) X(vkCreateImage) X(vkDestroyImage) X(vkGetImageMemoryRequirements)                  \
  X(vkCreateBuffer) X(vkDestroyBuffer) X(vkGetBufferMemoryRequirements) X(vkAllocateMemory) X(vkFreeMemory)                 \
  X(vkBindImageMemory) X(vkBindBufferMemory) X(vkMapMemory) X(vkUnmapMemory) X(vkInvalidateMappedMemoryRanges)                \
  X(vkCreateCommandPool) X(vkDestroyCommandPool) X(vkAllocateCommandBuffers) X(vkBeginCommandBuffer) X(vkEndCommandBuffer)   \
  X(vkCmdPipelineBarrier) X(vkCmdClearColorImage) X(vkCmdCopyImageToBuffer) X(vkCreateFence) X(vkDestroyFence)              \
  X(vkQueueSubmit) X(vkWaitForFences) X(vkDeviceWaitIdle)

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

int probe_vulkan(const char *library)
{
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
                                 .apiVersion = VK_API_VERSION_1_0};
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

  VkQueueFamilyProperties families[8];
  uint32_t familyCount = 8, family = UINT32_MAX;
  p_vkGetPhysicalDeviceQueueFamilyProperties(physical, &familyCount, families);
  for (uint32_t i = 0; i < familyCount && family == UINT32_MAX; i++)
    if (families[i].queueFlags & (VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT | VK_QUEUE_TRANSFER_BIT)) family = i;
  snprintf(detail, sizeof detail, "family %u of %u, flags 0x%x", family, familyCount, family < familyCount ? families[family].queueFlags : 0);
  if (!vkCheck("queue family", family != UINT32_MAX, detail)) goto out_instance;

  VkFormatProperties format;
  p_vkGetPhysicalDeviceFormatProperties(physical, VK_FORMAT_R8G8B8A8_UNORM, &format);
  snprintf(detail, sizeof detail, "optimal features 0x%x", format.optimalTilingFeatures);
  vkCheck("R8G8B8A8_UNORM transfer", (format.optimalTilingFeatures & (VK_FORMAT_FEATURE_TRANSFER_SRC_BIT |
                                      VK_FORMAT_FEATURE_TRANSFER_DST_BIT)) == (VK_FORMAT_FEATURE_TRANSFER_SRC_BIT |
                                      VK_FORMAT_FEATURE_TRANSFER_DST_BIT), detail);

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
  const VkDeviceSize bytes = (VkDeviceSize)kWidth * kHeight * 4;

  const VkImageCreateInfo imageInfo = {
      .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .imageType = VK_IMAGE_TYPE_2D, .format = VK_FORMAT_R8G8B8A8_UNORM,
      .extent = {kWidth, kHeight, 1}, .mipLevels = 1, .arrayLayers = 1, .samples = VK_SAMPLE_COUNT_1_BIT,
      .tiling = VK_IMAGE_TILING_OPTIMAL, .usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT,
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
  void *mapped = NULL;
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
    vkStep("vkInvalidateMappedMemoryRanges", p_vkInvalidateMappedMemoryRanges(device, 1, &whole), NULL);
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
  if (vkCheck("clear readback", match == kWidth * kHeight, detail)) result = 0;

out_objects:
  if (device) p_vkDeviceWaitIdle(device);
  if (fence) p_vkDestroyFence(device, fence, NULL);
  if (pool) p_vkDestroyCommandPool(device, pool, NULL);
  if (bufferMemory) p_vkUnmapMemory(device, bufferMemory);
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

