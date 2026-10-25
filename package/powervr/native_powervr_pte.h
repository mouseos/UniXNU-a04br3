/* SPDX-License-Identifier: MIT */
#ifndef NATIVE_POWERVR_PTE_H
#define NATIVE_POWERVR_PTE_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
struct native_powervr_vm_context;
struct native_powervr_pte_snapshot;
/* Values only; no page-table pointer survives the shared pmap lock. */
struct native_powervr_pte_alias {
  uint64_t va, pa, entry, root, mair;
  uint32_t status, level, attr, sh;
};
enum { NATIVE_PVR_PTE_TARGETS = 3 };
struct native_powervr_bo_policy_record {
  uint64_t size;
  uint32_t ready, initialized, enabled, supplied, requested;
  uint32_t allocation_options, descriptor_flags, descriptor_present;
};
struct native_powervr_pte_target {
  uint64_t gpu, mapping_va, mapping_size, bo_offset, expected_pa;
  uint32_t status, map_options;
  struct native_powervr_pte_alias user, kernel, physmap;
};
struct native_powervr_pte_record {
  uint64_t fence, submission;
  uint32_t job, seqno, ctx, index;
  struct native_powervr_bo_policy_record first_bo;
  struct native_powervr_pte_target target[NATIVE_PVR_PTE_TARGETS];
};
int native_powervr_pte_enabled(void);
/* Prepare may allocate/reference existing mappings, but never maps new memory.
 * PDS/USC addresses are fixed probes observed in the controlled SIPF1 workload,
 * not a decode of the contents of a PDS instruction or proof of its target. */
struct native_powervr_pte_snapshot *native_powervr_pte_prepare(struct native_powervr_vm_context *, uint64_t sipf);
void native_powervr_pte_put(struct native_powervr_pte_snapshot *);
void native_powervr_pte_capture(struct native_powervr_pte_snapshot *, struct native_powervr_pte_record *);
void native_powervr_pte_validate(struct native_powervr_pte_snapshot *, struct native_powervr_pte_record *);
typedef int (*native_powervr_pte_phase)(void *, struct native_powervr_pte_snapshot **,
                                      struct native_powervr_pte_record **, uint32_t);
void *native_powervr_pte_map_get(void *task);
void native_powervr_pte_map_put(void *map);
void native_powervr_pte_walk(void *map, uint64_t va, struct native_powervr_pte_alias *);
#ifdef __cplusplus
}
#endif
#endif
