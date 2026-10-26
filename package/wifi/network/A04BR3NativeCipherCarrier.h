/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * iOS16 SET3's proven carrier ONLY. REA SetWithIOCTL case3 requires exactly
 * 0x94 bytes and forwards the original bytes; no key-field interpretation.
 * See NativeSecuritySources.json. A decoder/crypto owner is still required.
 */
#ifndef A04BR3_NATIVE_CIPHER_CARRIER_H
#define A04BR3_NATIVE_CIPHER_CARRIER_H
#include <stdint.h>
#include <stddef.h>
#include <sys/errno.h>
#define A04_NATIVE_CIPHER_KEY_TYPE 3u
#define A04_NATIVE_CIPHER_KEY_BYTES 148u
struct a04_native_cipher_view {const uint8_t *bytes;unsigned length;};
static inline int a04_native_cipher_carrier(uint32_t type,uint32_t value,
    const void *raw,unsigned bytes,struct a04_native_cipher_view *out)
{
    if(!out)return EINVAL;
    out->bytes=NULL;out->length=0;
    if(type!=A04_NATIVE_CIPHER_KEY_TYPE || value)return EOPNOTSUPP;
    if(!raw || bytes!=A04_NATIVE_CIPHER_KEY_BYTES ||
        (uintptr_t)raw>UINTPTR_MAX-A04_NATIVE_CIPHER_KEY_BYTES)return EINVAL;
    out->bytes=(const uint8_t *)raw;out->length=A04_NATIVE_CIPHER_KEY_BYTES;return 0;
}
/* A byte-preserving scoped handoff, NOT a successful key install. The supplied
 * consumer must validate its independently proven field/cipher semantics and
 * invoke the existing key backend; absent/unimplemented -> EOPNOTSUPP.
 * No authorize callback exists here. Key SET must never open a controlled port.
 * The consumer cannot retain scratch or run asynchronously without an owned copy.
 */
struct a04_native_cipher_consumer {
    void *context;
    int (*decode_and_install)(void *,const uint8_t *,unsigned);
};
static inline int a04_native_cipher_dispatch(uint32_t type,uint32_t value,
    const void *raw,unsigned bytes,const struct a04_native_cipher_consumer *consumer)
{
    struct a04_native_cipher_view view;
    int error=a04_native_cipher_carrier(type,value,raw,bytes,&view);if(error)return error;
    if(!consumer || !consumer->decode_and_install)return EOPNOTSUPP;
    uint8_t scratch[A04_NATIVE_CIPHER_KEY_BYTES];
    for(unsigned i=0;i<sizeof(scratch);i++)scratch[i]=view.bytes[i];
    error=consumer->decode_and_install(consumer->context,scratch,sizeof(scratch));
    volatile uint8_t *wipe=scratch;for(unsigned i=0;i<sizeof(scratch);i++)wipe[i]=0;
    return error;
}

/* Field offsets are now corroborated by the actual iOS16 eapolclient SET3
 * producers (WEP and MSK/PMKSA), plus __performAssociation's embedded secret.
 * Origin is a private adapter enum, NOT an Apple request field. Keep the two
 * producers distinct: no CCMP/TK16 or key-install success is inferred here. */
enum a04_native_key_origin { A04_NATIVE_KEY_FROM_SET3=1,A04_NATIVE_KEY_FROM_ASSOC20=2 };
enum a04_native_key_kind {
    A04_NATIVE_KEY_WEP40=1,A04_NATIVE_KEY_WEP104=2,
    A04_NATIVE_KEY_MSK=3,A04_NATIVE_KEY_PMKSA=4,
    A04_NATIVE_KEY_PMK=5,A04_NATIVE_KEY_PASSPHRASE=6
};
struct a04_native_key_fields {
    unsigned kind,length,flags,index;
    uint8_t secret[64],peer[6];
};
static inline uint32_t a04_native_key_le32(const uint8_t *p)
{return p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static inline void a04_native_key_clear(struct a04_native_key_fields *out)
{if(out){volatile uint8_t *p=(volatile uint8_t *)out;for(unsigned i=0;i<sizeof(*out);i++)p[i]=0;}}
static inline int a04_native_key_fields_decode(const void *raw,unsigned bytes,
    unsigned origin,struct a04_native_key_fields *out)
{
    if(!out)return EINVAL;
    a04_native_key_clear(out);
    if(!raw || bytes!=148 || (uintptr_t)raw>UINTPTR_MAX-148)return EINVAL;
    if(origin!=A04_NATIVE_KEY_FROM_SET3 && origin!=A04_NATIVE_KEY_FROM_ASSOC20)return EINVAL;
    const uint8_t *p=(const uint8_t *)raw;
    if(a04_native_key_le32(p)!=1)return EOPNOTSUPP;
    unsigned n=a04_native_key_le32(p+4),cipher=a04_native_key_le32(p+8);
    unsigned flags=p[12]|((unsigned)p[13]<<8),index=p[14]|((unsigned)p[15]<<8),kind=0;
    if(origin==A04_NATIVE_KEY_FROM_SET3){
        if((cipher==1 && n==5) || (cipher==2 && n==13)){
            if(flags!=1 && flags!=2 && flags!=5 && flags!=9)return EOPNOTSUPP;
            kind=cipher==1?A04_NATIVE_KEY_WEP40:A04_NATIVE_KEY_WEP104;
        }else if((cipher==7 || cipher==9) && (n==0 || n==64)){
            if(flags || index)return EOPNOTSUPP;
            kind=cipher==7?A04_NATIVE_KEY_PMKSA:A04_NATIVE_KEY_MSK;
        }else return EOPNOTSUPP;
    }else{
        if(flags || index)return EOPNOTSUPP;
        if(cipher==6 && n==32)kind=A04_NATIVE_KEY_PMK;
        else if(cipher==10 && n>=8 && n<=63)kind=A04_NATIVE_KEY_PASSPHRASE;
        else return EOPNOTSUPP;
    }
    /* Producers zero their full148-byte record, with only these known stores.
     * Do not interpret RSC/WoWL/counter fields merely from a third-party struct.
     * A nonzero unsupported extension rejects the whole request. */
    for(unsigned i=16+n;i<148;i++){
        if(kind==A04_NATIVE_KEY_PMKSA && i>=92 && i<98)continue;
        if(p[i])return EOPNOTSUPP;
    }
    if(kind==A04_NATIVE_KEY_PMKSA){
        unsigned any=0;for(unsigned i=0;i<6;i++)any|=p[92+i];
        if(!any || (p[92]&1))return EINVAL;
    }
    out->kind=kind;out->length=n;out->flags=flags;out->index=index;
    for(unsigned i=0;i<n;i++)out->secret[i]=p[16+i];
    if(kind==A04_NATIVE_KEY_PMKSA)for(unsigned i=0;i<6;i++)out->peer[i]=p[92+i];
    return 0;
}
/* Proven embedded key starts at56 in the exact592-byte association carrier.
 * This reads secret fields only; existing association admission and its actual
 * peer/policy/authentication checks remain necessary and are not modified. */
static inline int a04_native_assoc_key_fields(const void *raw,unsigned bytes,
    struct a04_native_key_fields *out)
{
    if(!raw || bytes!=592 || (uintptr_t)raw>UINTPTR_MAX-592){a04_native_key_clear(out);return EINVAL;}
    const uint8_t *p=(const uint8_t *)raw;
    if(a04_native_key_le32(p)!=1){a04_native_key_clear(out);return EOPNOTSUPP;}
    return a04_native_key_fields_decode(p+56,148,A04_NATIVE_KEY_FROM_ASSOC20,out);
}
#endif
