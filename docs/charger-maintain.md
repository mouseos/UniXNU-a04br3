# A04BR3 charge maintenance (a04br3-charger-maintain=1)

Default off. Without the exact boot argument the board init returns before any
mapping, allocation or I2C access, as for a04br3-charger-owner.

## Why the watchdog owner stopped

Overnight UART runs show, on every boot:

    a04br3 charger: firmware handoff verified last=293268740 exit=397065414 now=406760744 freq=13000000 ...
    a04br3 charger: t=31323298769 valid=ff raw=30,00,8c,a4,45,51,00,38 result=1 kicks=0 firstFAULT=0 history=01
    a04br3 charger: owner stopped; no policy repair/retry; 30 s WDT fallback may alter inherited settings

result=1 is Deadline. The firmware handoff was valid (last firmware pet 8.7 s
before adoption, about 11 s of budget left) and the registers equal the LK/stock
image, so neither the handoff nor the policy rejected it. A04BR3ChargerCore.h
step() also returns Deadline when one 8-register snapshot takes 1 s or more.
Images that carry the bounded retry (998475f, A04BR3ChargerFresh.h) also log
per-read timestamps. In the same overnight UART log every boot shows the first
attempt crossing that bound, for example:

    a04br3 charger: attempts=2 ... step=31289845692/33570714000 ... read0=33534355000/33534771076 read1=.../33535186615 read2=33535186769/33545506538

The default-priority (BASEPRI_KERNEL 81) owner started at 31.29 s while boot
work such as the 1757 ms NativeMsdc root-policy read was running, and its
second attempt only began reading at 33.53 s, so the first snapshot took about
2.2 s. With the retry those images kept kicking (up to kicks=720 per boot);
the later memory-low-impact images were built without 998475f, so each boot
printed result=1 kicks=0 and the owner latched stopped before its first kick.
With no further TMR_RST the BQ24160 host watchdog expires 30 s after the last
firmware pet and reloads DEFAULT settings (TI SLUSAO0H 8.3.9: lower regulation
voltage and charge current than the stock 4.32 V / 1.3 A). The battery then
supplies the iOS load that the adapter path no longer covers.

## What the maintenance mode does

It follows the stock CT3 BSP, which programs the charger once
(bq24160.c bq24160_charge_init) and then writes TMR_RST every 10 s while a
charger is present (sts_battery_common.c bat_thread, BAT_TASK_PERIOD):

| Field | Stock value | This mode |
| --- | --- | --- |
| HZ / CE / TE / EN_STAT | 0 / 0 (enabled) / 1 / 1 | same |
| IN input limit (reg03 bit1) | 0 = 1.5 A (2.5 A never used) | 1.5 A |
| USB input limit | 100 mA, 500 mA with a host | kept if <=500 mA, else clamped to 500 mA |
| Charge current ICHRG | 0x0a = 1.3 A | 1.3 A |
| Termination ITERM | 1 = 100 mA | 100 mA |
| Regulation VBREG | 0x29 = 4.32 V | 4.32 V |
| Safety timer / TS | 6 h, TS enabled | same |

The stock values are the ceiling; static_assert and host tests check that no
planned write exceeds them. Fields the stock driver never writes (SUPPLY_SEL,
OTG_LOCK, EN_NOBATOP, VINDPM, reg07 bit4) are preserved.

Each 10 s cycle, in one kernel thread at priority 91 (default kernel threads
run at 81) holding the I2C2 lock, with up to three attempts per cycle:

1. Before each attempt, if START is still set from a transfer this thread
   abandoned, clear it with the controller SOFTRESET (offset 0x50, as
   i2c-mt65xx init_hw does) and re-initialize I2C2. I2C2 carries only this
   thread's charger and gauge traffic.
2. Read register 00 and the 04 ID. If the TI family ID matches, write TMR_RST
   (with unchanged SUPPLY_SEL) at once, so a later read failure cannot delay
   the kick.
3. Read the remaining registers. A transfer error ends the attempt; the next
   attempt re-initializes the controller.
4. If FAULT is 0 or 3 (host watchdog expired) and the configuration differs
   from the stock image, write the differing registers and re-read them.
   Thermal, TS, safety-timer, supply and battery faults are logged and kicked
   but never overridden.
5. Only after the kick, and only when a line is due, read the fuel gauge. A
   gauge timeout is recovered at the start of the next attempt or cycle.

There is no 1 s snapshot deadline and no latched stop: a slow or failed cycle
is retried 10 s later, which is still inside the 30 s watchdog window.

Register 02 reads back RESET=1 (0x8c); that bit is excluded from comparison and
always written as 0. DDET_EN (register 03 bit0) is preserved. The stock driver
also writes TMR_RST every 10 s; whether TMR_RST restarts the 6 h safety timer
has not been verified here, so the timer behaves as it does under stock Linux.

## Telemetry

Every a04br3-charger-log seconds (default 60, 10-3600) and on any STAT/FAULT
change, repair or error, the thread also reads the LC709203F fuel gauge (0x0b,
read-only, CRC-8 checked as in stock lc709203f.c) and prints:

    a04br3 charger: maintain periodic t=95s kept stat=3 fault=0 raw=30,00,8c,a4,45,51,00,38 valid=ff kicks=7 worstgap=10012ms repairs=0 rerr=0 werr=0 history=01
    a04br3 battery: t=95s vbat=3812mV rsoc=57% ite=56.3% temp=25.0C

stat: 0 no source, 1 IN ready, 2 USB ready, 3 charging from IN, 4 charging
from USB, 5 done, 7 fault. rsoc is the gauge RSOC register; ite is the
indicator-to-empty value stock Android rescales for its UI. When a repair
happens a "maintain wrote" line lists the before image and every write.

## Enabling and checking

Add --kernel-arg a04br3-charger-maintain=1 (short alias a04br3-cm=1; log
period alias a04br3-cl=<seconds>) to the payload build (the
a04br3-memory-*payload.sh scripts pass extra arguments through). If
a04br3-charger-owner=1 is also present it is ignored and logged as such. The
firmware owner (A04BR3_CHARGER_OWNER=1) may stay enabled; it bridges LK to EBS,
and this mode does not need its handoff record.

On the device, confirm in UART: kicks increases by 1 per 10 s, worstgap stays
near 10000 ms (it must stay below 30000 ms), raw stays
30,00,8c,a4,45,51,00,38 (or stat 5 when full) and vbat/rsoc rise or stay level
under iOS load with the adapter connected. The first "maintain change" line
gives the time of the first kernel kick; "maintain retried" lines show
in-cycle retries and soft resets.

## Checks

    python3 scripts/tests/test_a04br3_charger_maintain.py
    python3 scripts/tests/test_a04br3_charger.py   # also syntax-checks the board .cpp for AArch64

These host tests run the actual header code against a simulated MT8167 I2C2
with a BQ24160 and LC709203F. They do not prove electrical behaviour.

