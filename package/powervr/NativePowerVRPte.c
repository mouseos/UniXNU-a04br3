/* SPDX-License-Identifier: MIT */
/* Device-only observation. Use XNU's lock and geometry, never switch TTBRs,
 * fault a page in, dereference a BO, or clean/invalidate any cache line. */
/* Match the pmap translation unit's prerequisite order. This file is built
 * with osfmk/DEVELOPMENT/pmap.o.json, not the BSD kern_exec record. */
#include <mach_assert.h>
#include <mach_ldebug.h>
#include <kern/thread.h>
#include <kern/sched.h>
#include <kern/spl.h>
#include <vm/pmap.h>
#include <vm/vm_map.h>
#include <arm/misc_protos.h>
#include <arm/pmap/pmap_internal.h>
#include <kern/task.h>
#include "native_powervr_pte.h"

void *native_powervr_pte_map_get(void *task)
{
  return get_task_map_reference((task_t)task);
}
void native_powervr_pte_map_put(void *map)
{
  if (map) vm_map_deallocate((vm_map_t)map);
}
static uint64_t pte_mair(void)
{
  uint64_t v;
  __asm__ volatile("mrs %0, mair_el1" : "=r"(v));
  return v;
}

void native_powervr_pte_walk(void *map, uint64_t va, struct native_powervr_pte_alias *out)
{
  const struct native_powervr_pte_alias empty = {0, 0, 0, 0, 0, 1, 0, 0, 0};
  *out = empty;
  out->va = va;
  if (!map || !va) return;
  pmap_t pmap = vm_map_pmap((vm_map_t)map);
  if (!pmap) return;
  pmap_lock(pmap, PMAP_LOCK_SHARED);
  out->root = pmap->ttep;
  out->mair = pte_mair();
  const pt_attr_t *geometry = pmap_get_pt_attr(pmap);
  if (va >= pmap->min && va < pmap->max) {
    tt_entry_t *table = pmap->tte;
    for (unsigned level = pt_attr_root_level(geometry); level <= pt_attr_leaf_level(geometry); ++level) {
      const uint64_t entry = *(volatile tt_entry_t *)&table[ttn_index(geometry, va, level)];
      out->level = level;
      out->entry = entry;
      if (!(entry & ARM_TTE_VALID)) break;
      const int leaf = level == pt_attr_leaf_level(geometry);
      if (leaf || (entry & ARM_TTE_TYPE_MASK) == ARM_TTE_TYPE_BLOCK) {
        /* Reserved L0 blocks and reserved L3 descriptors are not translations. */
        if ((!leaf && level == 0) || (leaf && !(entry & ARM_TTE_TYPE_MASK))) break;
        const uint64_t mask = pt_attr_ln_offmask(geometry, level);
        out->pa = (entry & ARM_TTE_PA_MASK & ~mask) | (va & mask);
        out->attr = (entry & ARM_PTE_ATTRINDXMASK) / ARM_PTE_ATTRINDX(1);
        out->sh = (entry & ARM_PTE_SHMASK) / ARM_PTE_SH(1);
        out->status = 0;
        break;
      }
      table = (tt_entry_t *)phystokv(entry & ARM_TTE_TABLE_MASK);
    }
  }
  pmap_unlock(pmap, PMAP_LOCK_SHARED);
}
