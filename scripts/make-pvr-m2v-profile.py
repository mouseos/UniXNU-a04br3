#!/usr/bin/env python3
"""Write a Khronos profiles JSON of the GE8300 as Mesa's PowerVR driver describes it, for the metal2vulkan translator's offline compiler
(metal2vulkan --profile), so the pipelines of a metallib (QuartzCore's default.metallib, for example) can be compiled on any host for
this GPU and every refusal named before hardware runs them.

  make-pvr-m2v-profile.py --base VK12_BARE.json --out PROFILE.json

VK12_BARE.json is metal2vulkan's crates/metal2vulkan-validation/profiles/vk12-bare.json (the Vulkan 1.2 floor: required features, minimum
limits and the required format table). This adds one capability with what the pinned Mesa (scripts/data/pvr-mesa-inputs.json,
src/imagination/vulkan/pvr_physical_device.c, plus patches/mesa/pvr-darwin-winsys.patch) reports for the device: its features, its
device extensions on Darwin (no fd, dma-buf, DRM or WSI extensions) and the limits that differ from the floor. Formats stay the floor's
required table: pvr_formats.c is not transcribed, so a format refusal from this profile is a lower bound and the device may support
more. Values are transcribed by hand from that source; rerun it after the Mesa pin moves."""
import argparse
import json
from pathlib import Path

FEATURES10 = dict(robustBufferAccess=True, fullDrawIndexUint32=True, imageCubeArray=True, independentBlend=True, geometryShader=False,
                  tessellationShader=False, sampleRateShading=True, dualSrcBlend=False, logicOp=True, multiDrawIndirect=True,
                  drawIndirectFirstInstance=True, depthClamp=False, depthBiasClamp=True, fillModeNonSolid=False, depthBounds=False,
                  wideLines=True, largePoints=True, alphaToOne=True, multiViewport=False, samplerAnisotropy=True,
                  textureCompressionETC2=True, textureCompressionASTC_LDR=False, textureCompressionBC=False, occlusionQueryPrecise=True,
                  pipelineStatisticsQuery=False, vertexPipelineStoresAndAtomics=False, fragmentStoresAndAtomics=True,
                  shaderTessellationAndGeometryPointSize=False, shaderImageGatherExtended=True, shaderStorageImageExtendedFormats=True,
                  shaderStorageImageMultisample=False, shaderStorageImageReadWithoutFormat=True, shaderStorageImageWriteWithoutFormat=True,
                  shaderUniformBufferArrayDynamicIndexing=True, shaderSampledImageArrayDynamicIndexing=True,
                  shaderStorageBufferArrayDynamicIndexing=True, shaderStorageImageArrayDynamicIndexing=True, shaderClipDistance=True,
                  shaderCullDistance=True, shaderFloat64=False, shaderInt64=False, shaderInt16=False, shaderResourceResidency=False,
                  shaderResourceMinLod=False, sparseBinding=False, variableMultisampleRate=False, inheritedQueries=False)
FEATURES11 = dict(storageBuffer16BitAccess=False, uniformAndStorageBuffer16BitAccess=False, storagePushConstant16=False,
                  storageInputOutput16=False, multiview=True, multiviewGeometryShader=False, multiviewTessellationShader=False,
                  variablePointersStorageBuffer=False, variablePointers=False, protectedMemory=False, samplerYcbcrConversion=True,
                  shaderDrawParameters=True)
FEATURES12 = dict(samplerMirrorClampToEdge=True, drawIndirectCount=False, storageBuffer8BitAccess=False,
                  uniformAndStorageBuffer8BitAccess=False, storagePushConstant8=False, shaderBufferInt64Atomics=False,
                  shaderSharedInt64Atomics=False, shaderFloat16=False, shaderInt8=False, descriptorIndexing=False,
                  shaderInputAttachmentArrayDynamicIndexing=True, shaderUniformTexelBufferArrayDynamicIndexing=True,
                  shaderStorageTexelBufferArrayDynamicIndexing=True, runtimeDescriptorArray=False, samplerFilterMinmax=False,
                  scalarBlockLayout=True, imagelessFramebuffer=True, uniformBufferStandardLayout=True, shaderSubgroupExtendedTypes=True,
                  separateDepthStencilLayouts=True, hostQueryReset=True, timelineSemaphore=True, bufferDeviceAddress=False,
                  vulkanMemoryModel=False, shaderOutputViewportIndex=False, shaderOutputLayer=False, subgroupBroadcastDynamicId=True)
# pvr_physical_device_get_supported_extensions() with PVR_WINSYS_HAS_FD and PVR_USE_WSI_PLATFORM false, and EXT_external_memory_host
# (the Darwin kernel interface's host import).
EXTENSIONS = [
    'VK_KHR_bind_memory2', 'VK_KHR_copy_commands2', 'VK_KHR_create_renderpass2', 'VK_KHR_dedicated_allocation', 'VK_KHR_depth_stencil_resolve',
    'VK_KHR_descriptor_update_template', 'VK_KHR_device_group', 'VK_KHR_driver_properties', 'VK_KHR_dynamic_rendering', 'VK_KHR_external_fence',
    'VK_KHR_external_memory', 'VK_KHR_external_semaphore', 'VK_KHR_format_feature_flags2', 'VK_KHR_get_memory_requirements2',
    'VK_KHR_global_priority', 'VK_KHR_image_format_list', 'VK_KHR_imageless_framebuffer', 'VK_KHR_index_type_uint8',
    'VK_KHR_line_rasterization', 'VK_KHR_maintenance1', 'VK_KHR_maintenance2', 'VK_KHR_maintenance3', 'VK_KHR_maintenance4',
    'VK_KHR_maintenance5', 'VK_KHR_maintenance7', 'VK_KHR_map_memory2', 'VK_KHR_multiview', 'VK_KHR_pipeline_executable_properties',
    'VK_KHR_push_descriptor', 'VK_KHR_relaxed_block_layout', 'VK_KHR_robustness2', 'VK_KHR_sampler_mirror_clamp_to_edge',
    'VK_KHR_sampler_ycbcr_conversion', 'VK_KHR_separate_depth_stencil_layouts', 'VK_KHR_shader_draw_parameters', 'VK_KHR_shader_expect_assume',
    'VK_KHR_shader_float_controls', 'VK_KHR_shader_float_controls2', 'VK_KHR_shader_fma', 'VK_KHR_shader_integer_dot_product',
    'VK_KHR_shader_non_semantic_info', 'VK_KHR_shader_relaxed_extended_instruction', 'VK_KHR_shader_subgroup_extended_types',
    'VK_KHR_shader_subgroup_rotate', 'VK_KHR_shader_subgroup_uniform_control_flow', 'VK_KHR_shader_terminate_invocation', 'VK_KHR_spirv_1_4',
    'VK_KHR_storage_buffer_storage_class', 'VK_KHR_synchronization2', 'VK_KHR_timeline_semaphore', 'VK_KHR_unified_image_layouts',
    'VK_KHR_uniform_buffer_standard_layout', 'VK_KHR_vertex_attribute_divisor', 'VK_KHR_workgroup_memory_explicit_layout',
    'VK_EXT_border_color_swizzle', 'VK_EXT_color_write_enable', 'VK_EXT_custom_border_color', 'VK_EXT_depth_clamp_zero_one',
    'VK_EXT_depth_clip_enable', 'VK_EXT_extended_dynamic_state', 'VK_EXT_extended_dynamic_state2', 'VK_EXT_extended_dynamic_state3',
    'VK_EXT_external_memory_host', 'VK_EXT_global_priority', 'VK_EXT_global_priority_query', 'VK_EXT_host_query_reset',
    'VK_EXT_image_2d_view_of_3d', 'VK_EXT_image_sliced_view_of_3d', 'VK_EXT_index_type_uint8', 'VK_EXT_inline_uniform_block',
    'VK_EXT_line_rasterization', 'VK_EXT_non_seamless_cube_map', 'VK_EXT_primitive_topology_list_restart', 'VK_EXT_private_data',
    'VK_EXT_provoking_vertex', 'VK_EXT_queue_family_foreign', 'VK_EXT_robustness2', 'VK_EXT_scalar_block_layout',
    'VK_EXT_separate_stencil_usage', 'VK_EXT_shader_demote_to_helper_invocation', 'VK_EXT_shader_replicated_composites',
    'VK_EXT_shader_subgroup_ballot', 'VK_EXT_shader_subgroup_vote', 'VK_EXT_subgroup_size_control', 'VK_EXT_texel_buffer_alignment',
    'VK_EXT_tooling_info', 'VK_EXT_transform_feedback', 'VK_EXT_vertex_attribute_divisor', 'VK_EXT_zero_initialize_device_memory',
    'VK_IMG_filter_linear_2d']
# The features of extensions the translator reads, as pvr_physical_device_get_supported_features() sets them.
EXTENSION_FEATURES = {
    'VkPhysicalDeviceSubgroupSizeControlFeaturesEXT': dict(subgroupSizeControl=True, computeFullSubgroups=True),
    'VkPhysicalDeviceShaderDemoteToHelperInvocationFeaturesEXT': dict(shaderDemoteToHelperInvocation=True),
    'VkPhysicalDeviceShaderTerminateInvocationFeaturesKHR': dict(shaderTerminateInvocation=True),
    'VkPhysicalDeviceRobustness2FeaturesEXT': dict(robustBufferAccess2=False, robustImageAccess2=False, nullDescriptor=True),
    'VkPhysicalDeviceMaintenance4FeaturesKHR': dict(maintenance4=True),
    'VkPhysicalDeviceMaintenance5FeaturesKHR': dict(maintenance5=True),
    'VkPhysicalDeviceSynchronization2FeaturesKHR': dict(synchronization2=True),
    'VkPhysicalDeviceDynamicRenderingFeaturesKHR': dict(dynamicRendering=True),
    'VkPhysicalDeviceInlineUniformBlockFeaturesEXT': dict(inlineUniformBlock=True, descriptorBindingInlineUniformBlockUpdateAfterBind=True),
    'VkPhysicalDeviceCustomBorderColorFeaturesEXT': dict(customBorderColors=True, customBorderColorWithoutFormat=False),
    'VkPhysicalDeviceIndexTypeUint8FeaturesEXT': dict(indexTypeUint8=True),
    'VkPhysicalDeviceTransformFeedbackFeaturesEXT': dict(transformFeedback=True, geometryStreams=False),
    'VkPhysicalDeviceShaderIntegerDotProductFeaturesKHR': dict(shaderIntegerDotProduct=True),
}
ROGUE_MAX_INSTANCES_PER_TASK = 32
PROPERTIES = {
    'VkPhysicalDeviceProperties': {'limits': dict(
        maxImageDimension1D=4096, maxImageDimension2D=4096, maxImageDimensionCube=4096, maxTexelBufferElements=65536,
        maxUniformBufferRange=16384, maxStorageBufferRange=128 * 1024 * 1024, maxPushConstantsSize=128, maxMemoryAllocationCount=4096,
        maxBoundDescriptorSets=4, maxPerStageDescriptorSamplers=16, maxPerStageDescriptorUniformBuffers=13,
        maxPerStageDescriptorStorageBuffers=16, maxPerStageDescriptorSampledImages=16, maxPerStageDescriptorStorageImages=4,
        maxPerStageDescriptorInputAttachments=4, maxPerStageResources=57, maxDescriptorSetSamplers=48, maxDescriptorSetUniformBuffers=36,
        maxDescriptorSetUniformBuffersDynamic=8, maxDescriptorSetStorageBuffers=12, maxDescriptorSetStorageBuffersDynamic=4,
        maxDescriptorSetSampledImages=48, maxDescriptorSetStorageImages=12, maxDescriptorSetInputAttachments=4, maxVertexInputAttributes=16,
        maxVertexInputBindings=16, maxVertexInputAttributeOffset=2047, maxVertexInputBindingStride=2048, maxVertexOutputComponents=64,
        maxFragmentInputComponents=64, maxFragmentOutputAttachments=4, maxFragmentDualSrcAttachments=0, maxFragmentCombinedOutputResources=4,
        maxComputeSharedMemorySize=16384, maxComputeWorkGroupCount=[65535, 65535, 65535], maxComputeWorkGroupInvocations=128,
        maxComputeWorkGroupSize=[128, 128, 64], maxSamplerLodBias=16.0, maxSamplerAnisotropy=16.0, maxViewports=1,
        maxViewportDimensions=[4096, 4096], viewportBoundsRange=[-8192.0, 8191.0], maxFramebufferWidth=4096, maxFramebufferHeight=4096,
        maxFramebufferLayers=256, maxColorAttachments=4, minTexelOffset=-8, maxTexelOffset=7, minTexelGatherOffset=-8, maxTexelGatherOffset=7,
        maxSampleMaskWords=1, timestampComputeAndGraphics=False)},
    'VkPhysicalDeviceVulkan11Properties': dict(subgroupSize=ROGUE_MAX_INSTANCES_PER_TASK, subgroupSupportedStages=['VK_SHADER_STAGE_COMPUTE_BIT'],
        subgroupSupportedOperations=['VK_SUBGROUP_FEATURE_BASIC_BIT', 'VK_SUBGROUP_FEATURE_VOTE_BIT', 'VK_SUBGROUP_FEATURE_ARITHMETIC_BIT',
                                     'VK_SUBGROUP_FEATURE_BALLOT_BIT', 'VK_SUBGROUP_FEATURE_SHUFFLE_BIT',
                                     'VK_SUBGROUP_FEATURE_SHUFFLE_RELATIVE_BIT', 'VK_SUBGROUP_FEATURE_CLUSTERED_BIT'],
        subgroupQuadOperationsInAllStages=False, maxPerSetDescriptors=1024, maxMultiviewInstanceIndex=(1 << 27) - 1),
    'VkPhysicalDeviceVulkan12Properties': dict(
        denormBehaviorIndependence='VK_SHADER_FLOAT_CONTROLS_INDEPENDENCE_32_BIT_ONLY',
        roundingModeIndependence='VK_SHADER_FLOAT_CONTROLS_INDEPENDENCE_NONE', shaderSignedZeroInfNanPreserveFloat32=True,
        shaderDenormPreserveFloat32=False, shaderDenormFlushToZeroFloat32=False, maxTimelineSemaphoreValueDifference=18446744073709551615,
        maxUpdateAfterBindDescriptorsInAllPools=0, filterMinmaxSingleComponentFormats=False, filterMinmaxImageComponentMapping=False,
        supportedDepthResolveModes=['VK_RESOLVE_MODE_SAMPLE_ZERO_BIT'], supportedStencilResolveModes=['VK_RESOLVE_MODE_SAMPLE_ZERO_BIT'],
        independentResolveNone=True, independentResolve=True),
    'VkPhysicalDeviceSubgroupSizeControlPropertiesEXT': dict(minSubgroupSize=ROGUE_MAX_INSTANCES_PER_TASK, maxSubgroupSize=ROGUE_MAX_INSTANCES_PER_TASK,
        maxComputeWorkgroupSubgroups=128 // ROGUE_MAX_INSTANCES_PER_TASK, requiredSubgroupSizeStages=['VK_SHADER_STAGE_COMPUTE_BIT']),
    'VkPhysicalDevicePushDescriptorPropertiesKHR': dict(maxPushDescriptors=32),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads(args.base.read_text())
    name, entry = next(iter(profile['profiles'].items()))
    profile['capabilities']['pvr_ge8300_mesa'] = {
        'extensions': {e: 1 for e in EXTENSIONS},
        'features': {'VkPhysicalDeviceFeatures': FEATURES10, 'VkPhysicalDeviceVulkan11Features': FEATURES11,
                     'VkPhysicalDeviceVulkan12Features': FEATURES12, **EXTENSION_FEATURES},
        'properties': PROPERTIES,
    }
    entry['capabilities'] = list(entry['capabilities']) + ['pvr_ge8300_mesa']
    entry['label'] = 'PowerVR GE8300 (BVNC 22.40.54.30), Mesa PowerVR Vulkan on the NativePowerVR Darwin interface'
    entry['description'] = 'make-pvr-m2v-profile.py of UniXNU-a04br3 over ' + name
    profile['profiles'] = {'VP_UNIXNU_a04br3_pvr_ge8300': entry}
    args.out.write_text(json.dumps(profile, indent=1) + '\n')
    print('wrote', args.out)


if __name__ == '__main__':
    main()
