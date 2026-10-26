/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * MT8167 board MSDC2 PUPD/R1/R0 layout. Do not touch MSDC0/1 pads.
 */
#ifndef A04BR3_SDIO_PADS_H
#define A04BR3_SDIO_PADS_H
static void a04_sdio_pad_pulls(void *ctx,void (*write)(void *,unsigned,uint32_t))
{
 /* DAT0/1/2 GPIO70/71/72: pull-up, R1R0=01. */
 write(ctx,0xe48,(1u<<6)|(1u<<5)|(1u<<10)|(1u<<9)|(1u<<14)|(1u<<13));
 write(ctx,0xe44,(1u<<4)|(1u<<8)|(1u<<12));
 /* CMD GPIO68 and DAT3 GPIO73: pull-up, R1R0=01.
  * CLK GPIO69: pull-down, R1R0=10. Preserve GPIO14 field[14:12]. */
 write(ctx,0xe58,(1u<<10)|(1u<<9)|(1u<<2)|(1u<<1)|(1u<<4));
 write(ctx,0xe54,(1u<<8)|1u|(1u<<6)|(1u<<5));
}
#endif
