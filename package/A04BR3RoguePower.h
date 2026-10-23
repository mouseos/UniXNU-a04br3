/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Copied unchanged (below this header) from github.com/mouseos/xnu_kernel_benesse_a04br3 iokit/Kernel/arm/rogue/A04BR3RoguePower.h.
 * Bounded MT8167 MFG domain sequencing. Register facts from board Linux
 * mtk-scpsys-mt8167/mtk-infracfg/clk-mt8167, not the MT8173 status bits.
 */
#ifndef A04BR3_ROGUE_POWER_H
#define A04BR3_ROGUE_POWER_H
struct a04_rgx_io {
    void *ctx;
    uint32_t (*read)(void *,unsigned,unsigned);
    void (*write)(void *,unsigned,unsigned,uint32_t);
    void (*delay)(void *,unsigned);
};
enum { A04_RGX_SPM, A04_RGX_INFRA, A04_RGX_TOP, A04_RGX_MFG };
static int a04_rgx_wait(const struct a04_rgx_io *io,unsigned bank,unsigned reg,uint32_t mask,uint32_t value)
{
    for(unsigned n=0;n<10000;n++){
        if((io->read(io->ctx,bank,reg)&mask)==value)return 0;
        io->delay(io->ctx,10);
    }
    return 1;
}
static int a04_rgx_domain(const struct a04_rgx_io *io,unsigned off,uint32_t status,int sram)
{
    uint32_t v=io->read(io->ctx,A04_RGX_SPM,off);
    io->write(io->ctx,A04_RGX_SPM,off,v|4);v|=12;
    io->write(io->ctx,A04_RGX_SPM,off,v);
    if(a04_rgx_wait(io,A04_RGX_SPM,0x60c,status,status) ||
       a04_rgx_wait(io,A04_RGX_SPM,0x610,status,status))return 1;
    v&=~16u;io->write(io->ctx,A04_RGX_SPM,off,v);
    v&=~2u;io->write(io->ctx,A04_RGX_SPM,off,v);
    v|=1;io->write(io->ctx,A04_RGX_SPM,off,v);
    if(sram){v&=~0xf00u;io->write(io->ctx,A04_RGX_SPM,off,v);
        if(a04_rgx_wait(io,A04_RGX_SPM,off,0xf000,0))return 2;}
    return 0;
}
static int a04_rgx_power_on(const struct a04_rgx_io *io)
{
    /* Non-glitch-free AXI/slow muxes must be gated before selection.
     * Probe at crystal speed; no PLL programming or VCORE changes.
     * CLK_MUX_SEL0[10:8]=4 -> clk26m, SEL1[13:8]=8 -> csw_mux_mfg.
     */
    io->write(io->ctx,A04_RGX_TOP,0xa0,(1u<<6)|(1u<<7));
    io->write(io->ctx,A04_RGX_TOP,0x50,1u<<2);
    uint32_t mux=io->read(io->ctx,A04_RGX_TOP,0x000);
    io->write(io->ctx,A04_RGX_TOP,0x000,(mux&~(7u<<8))|(4u<<8));
    mux=io->read(io->ctx,A04_RGX_TOP,0x040);
    io->write(io->ctx,A04_RGX_TOP,0x040,mux&~((3u<<18)|(3u<<20)));
    io->write(io->ctx,A04_RGX_TOP,0x80,1u<<2);
    mux=io->read(io->ctx,A04_RGX_TOP,0x004);
    io->write(io->ctx,A04_RGX_TOP,0x004,(mux&~(63u<<8))|(8u<<8));
    io->write(io->ctx,A04_RGX_TOP,0xb0,(1u<<6)|(1u<<7));
    if(a04_rgx_domain(io,0x2c4,1u<<25,0))return 1;
    uint32_t v=io->read(io->ctx,A04_RGX_INFRA,0x204);
    io->write(io->ctx,A04_RGX_INFRA,0x204,v|(1u<<7));
    if(a04_rgx_domain(io,0x2c0,1u<<24,1))return 2;
    v=io->read(io->ctx,A04_RGX_INFRA,0x220);
    io->write(io->ctx,A04_RGX_INFRA,0x220,v&~0x24u);
    if(a04_rgx_wait(io,A04_RGX_INFRA,0x228,0x24,0))return 3;
    if(a04_rgx_domain(io,0x214,1u<<4,1))return 4;
    /* MFG registers are never accessed before all power/SRAM acknowledgments. */
    io->write(io->ctx,A04_RGX_MFG,8,0xf);
    if(a04_rgx_wait(io,A04_RGX_MFG,0,0xf,0))return 5;
    return 0;
}
#endif
