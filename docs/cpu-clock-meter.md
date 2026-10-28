# MT8167 ARMPLL 周波数計の限定実験

既定ではオフ。`cm=1` と `a04br3-home-perf=1`、process log の周期指定を明示したときだけ、heartbeat から一回ずつ試行する。PMUレジスタは使わず、IRQは停止しない。CPU・eMMC・メモリ・GPUの記録と同じ起動に含められる。

参照した実装は CT3 GPL source の `kernel-4.4/drivers/clk/mediatek/clkdbg_mt8167.c`、commit `7c4782127d4ea11965be00ce1343056be9bfb769`、SHA-256 `070d2b538c0ebde518a7f97eadcafd8c90e1de997132727fa079ed9995b5faac`。その `mcusys_debug_mon0` は CKGEN 25、debug selector 0 で ARMPLL、固定基準 26 MHz、計測窓 520 と記載される。Linux の CPU clock tree は infracfg mux 1 を ARMPLL に接続する。DCM の mode 1 は infracfg `0x14` bit 0 である。

この実験は PLL hopping、PLL test、CPU mux、debug selector、DCM を変更しない。hopping 全体が0、debug下位8bitが0、CPU muxが1、DCM bit 0が0、PLL有効かつmeter controlが0のときだけ開始する。meter controlのみを使い、resetとidleの読み戻し、ゼロのbaseline、測定中の設定読み戻し、最後にidle 0への復元と周辺レジスタの前後一致を要求する。任意の失敗では周波数を成功とせず、予期しないcontrol変化を見た場合には他主体の状態を上書きしない。実際の読み戻し・baseline・観測周波数・復元動作は実機で未確認であり、この保守的な条件では計測拒否になる可能性がある。

ログの `armpll_khz` は、26 MHz基準を仮定したARMPLLの測定候補であり、個々のcoreの実効クロックを証明しない。Linuxの記載、muxとDCMが示すのはクロック経路の条件であり、各CPUのgatingや未知の分周器、基準時計の実精度は未確認。ローカル排他はこの実装の呼出し間だけで、firmware、上位EL、external debuggerの排他所有を証明しない。状態変化と復元完了はMMIOの読み戻しに基づく観測であり、変更後に復元された競合は検知できない。実機の値、メモリ/eMMC負荷との同時性、表示動作、watchdog復帰を成功と扱うには初回起動ログが必要。

ホスト試験は `c++ -std=c++17 -O2 -Wall -Wextra -Werror scripts/tests/test_cpu_clock_meter.cpp -o /tmp/a04-cpu-meter-test && /tmp/a04-cpu-meter-test`。mockの排他、未設定拒否、control復元、値0、外部変更、PLL条件変化を検証する。実装は実機で未検証。
