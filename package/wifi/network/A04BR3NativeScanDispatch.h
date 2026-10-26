/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Actual native scan request/result state machine, with explicit real backend
 * operations. Production Rendezvous uses the SDIO adapter and its actual
 * generation-bound snapshot/copy_record API. No completion on SET success.
 */
#ifndef A04BR3_NATIVE_SCAN_DISPATCH_H
#define A04BR3_NATIVE_SCAN_DISPATCH_H
#include "A04BR3NativeScanResults.h"
struct a04_scan_backend {
    void *context;
    int (*begin)(void *,unsigned active,uint64_t *generation);
    int (*snapshot)(void *,uint64_t expected,unsigned index,
        struct a04_scan_snapshot *,struct a04_scan_observation *);
};
/* Caller serializes this state with its scan lock. Reserve before dropping
 * that lock to publish through DLIL; SET must use this same state. A concurrent
 * fresh SET returns EBUSY, never waits under the BSD bridge gate for an event
 * publisher which may need that gate. This prevents an older completion from
 * satisfying a newer accepted scan without introducing lock inversion.
 */
struct a04_scan_completion_state { uint64_t posted,publishing; };
static inline int a04_scan_dispatch_set(struct a04_scan_result_session *session,
    const struct a04_scan_backend *backend,uint32_t type,uint32_t value,
    const void *in,uint32_t bytes,const struct a04_scan_completion_state *events)
{
    if(type!=10)return EOPNOTSUPP;
    if(!session || !backend || !backend->snapshot || !events)return EINVAL;
    struct a04_scan_request request={};
    int error=a04_scan_result_request(value,in,bytes,&request);
    if(error)return error;
    unsigned mode=request.mode;
    struct a04_scan_snapshot snapshot={};
    if(mode==3){
        error=backend->snapshot(backend->context,0,0,&snapshot,nullptr);
        if(error)return error;
        error=a04_scan_result_accept(session,&snapshot,1);
        if(!error){session->filtered=request.filtered;session->channels=request.channels;}
        return error;
    }
    if(events->publishing)return EBUSY;
    if(!backend->begin)return EOPNOTSUPP;
    uint64_t generation=0;
    error=backend->begin(backend->context,mode==1?1u:0u,&generation);
    if(error)return error; // real errno, no unsupported mode success
    if(!generation)return EIO; // backend violated accepted-start contract
    snapshot.generation=generation;
    error=a04_scan_result_accept(session,&snapshot,0);
    if(!error){session->filtered=request.filtered;session->channels=request.channels;}
    return error;
}
static inline int a04_scan_dispatch_get(struct a04_scan_result_session *session,
    const struct a04_scan_backend *backend,uint32_t type,uint32_t value,
    void *out,uint32_t *bytes)
{
    if(type!=11 || value)return EOPNOTSUPP;
    if(!session || !session->generation || !backend || !backend->snapshot)return EINVAL;
    if(!out || !bytes || *bytes!=1164)return EINVAL;
    const uint8_t *p=(const uint8_t *)out;
    if(!a04_scan_result_get_header(p))return EINVAL;
    struct a04_scan_snapshot snapshot={};
    int error=backend->snapshot(backend->context,session->generation,session->cursor,&snapshot,nullptr);
    if(error)return error;
    if(snapshot.generation!=session->generation)return ESTALE;
    if(!snapshot.complete)return EAGAIN;
    // Records outside a requested channel list are skipped, never encoded.
    for(;;){
        if(session->cursor>=snapshot.count)
            return a04_scan_result_next(session,&snapshot,nullptr,out,bytes);
        struct a04_scan_observation record={};
        error=backend->snapshot(backend->context,session->generation,session->cursor,&snapshot,&record);
        if(error)return error;
        unsigned why=0;
        if(a04_scan_result_selected(session->filtered,session->channels,&record)){
            int check=snapshot.complete && record.generation==snapshot.generation ?
                a04_scan_result_record_check(&snapshot,&record,&why) : 0;
            if(check!=EINVAL && check!=EOPNOTSUPP)
                return a04_scan_result_next(session,&snapshot,&record,out,bytes);
            session->skipped++;session->skip_why=why;
        }
        if(snapshot.generation!=session->generation)return ESTALE;
        if(!snapshot.complete)return EAGAIN;
        if(session->pinned && snapshot.count!=session->count)return ESTALE;
        session->count=snapshot.count;session->pinned=1;session->cursor++;
    }
}
static inline unsigned a04_scan_completion_due(const struct a04_scan_completion_state *state,
    const struct a04_scan_snapshot *snapshot)
{
    return state && snapshot && snapshot->generation && snapshot->complete &&
        snapshot->generation>state->posted;
}
static inline unsigned a04_scan_completion_reserve(struct a04_scan_completion_state *state,
    const struct a04_scan_snapshot *snapshot)
{
    if(!a04_scan_completion_due(state,snapshot) || state->publishing)return 0;
    state->publishing=snapshot->generation;return 1;
}
/* Ack ONLY when DLIL publication succeeded. A failed post can be retried on a
 * later real changed callback; no timer pretending the firmware has finished.
 */
static inline void a04_scan_completion_posted(struct a04_scan_completion_state *state,
    uint64_t generation,int error)
{
    if(!state || state->publishing!=generation)return;
    if(!error && generation>state->posted)state->posted=generation;
    state->publishing=0;
}
#endif
