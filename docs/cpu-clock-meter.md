# MT8167 ARMPLL 周波数計の限定実験

既定ではオフ。`cm=1` と `a04br3-home-perf=1`、process log の周期指定を明示したときだけ、heartbeat から一回ずつ試行する。PMUレジスタは使わず、IRQは停止しない。PMUの `arc=1` または長い有効指定と同時に要求された場合は周波数計を起動せず、別bootを要求する。CPU・eMMC・メモリ・GPUの記録と同じ起動に含められる。

参照した実装は CT3 GPL source の `kernel-4.4/drivers/clk/mediatek/clkdbg_mt8167.c`、commit `7c4782127d4ea11965be00ce1343056be9bfb769`、SHA-256 `070d2b538c0ebde518a7f97eadcafd8c90e1de997132727fa079ed9995b5faac`。その `mcusys_debug_mon0` は CKGEN 25、debug selector 0 で ARMPLL、固定基準 26 MHz、計測窓 520 と記載される。Linux の CPU clock tree は infracfg mux 1 を ARMPLL に接続する。DCM の mode 1 は infracfg `0x14` bit 0 である。

この実験は PLL hopping、PLL test、CPU mux、debug selector、DCM を変更しない。hopping のARMPLL bit 0が0、PLL_TEST_CON1の下位10bitが0x287、debug下位8bitが0、CPU muxが1、DCM bit 0が0、PLL有効かつmeter controlが0のときだけ開始する。meter controlのみを使い、resetとidleの読み戻し、ゼロのbaseline、測定中の設定読み戻しと10 µs後の再読取一致、最後にidle 0への復元と周辺レジスタの前後一致を要求する。任意の失敗では周波数を成功とせず、予期しないcontrol変化を見た場合には他主体の状態を上書きしない。操作前にRTCとwatchdog modeを再確認し、書込み後にcontrol状態を確認できない場合はこのbootで再試行せずwatchdog給餌停止を維持する。ただしwatchdogが実機でfastbootへ復帰することは未検証。値の一致は完了フラグの代わりに設けた保守的な条件であり、完了そのものの証明ではない。実際の読み戻し・baseline・観測周波数・復元動作は実機で未確認であり、この保守的な条件では計測拒否になる可能性がある。

ログの `armpll_khz` は、26 MHz基準を仮定したARMPLLの測定候補であり、個々のcoreの実効クロックを証明しない。Linuxの記載、muxとDCMが示すのはクロック経路の条件であり、各CPUのgatingや未知の分周器、基準時計の実精度は未確認。ローカル排他はこの実装の呼出し間だけで、firmware、上位EL、external debuggerの排他所有を証明しない。状態変化と復元完了はMMIOの読み戻しに基づく観測であり、変更後に復元された競合は検知できない。実機の値、メモリ/eMMC負荷との同時性、表示動作、watchdog復帰を成功と扱うには初回起動ログが必要。

ホスト試験は `c++ -std=c++17 -O2 -Wall -Wextra -Werror scripts/tests/test_cpu_clock_meter.cpp -o /tmp/a04-cpu-meter-test && /tmp/a04-cpu-meter-test`。mockの排他、未設定拒否、control復元、値0、外部変更、PLL条件変化を検証する。実装は実機で未検証。

メーカのFHCTL定義はARMCA7 PLLをID 0に対応づける。他のhopping bitの存在を拒否しないが、全registerの前後値が変われば他のPLLによる変化も含めてstatus 7として保守的に棄却する。TEST_CON1とdebug selectorはread-onlyで扱い、測定実現のために設定を書き換えない。事前条件は非原子的で、途中で変わって元に戻る競合や、基準26 MHz・選択経路の正しさは未証明。TEST_CON1の設定が通常時に成立するかは未確認で、不成立なら測定を拒否する。

## 明示指定の比較計測 `cm=3`

既定はオフ。`cm=3` は `cm=2` と同じルート・watchdog の条件で一度だけ実行し、同じローカルロックとルートの下で CKGEN 25、7、24 を順に測る。`cm=1` と `cm=2` は従来どおり。`arc=1` などの PMU 指定とは併用しない。CT3 の名称はそれぞれ `mcusys_debug_mon0`、`g_univpll_d24_52m_ck`、`csw_52m_ck`。各回は reset、baseline、窓 519、40 µs 後と更に 10 µs 後の読み取り、reset と idle の読み戻しを行う。

`sample` ログは ID、attempted、status、stage、valid、baseline、count、second を出す。valid は baseline=1、count=2、second=4 のビットで、未取得と取得したゼロを区別する。stage は 0=準備、1=初期 reset、2=baseline、3=計測、4=最終 reset、5=周辺状態確認、6=成功。全体の `route_stage` は 0=未確立、1=PLL test 設定確認、2=debug 設定確認、3=debug 復元確認、4=PLL test 復元確認で、書込み失敗時の実際の状態を保証しない。各 sample の status は個別の測定結果、全体 status はルート復元等も含む。途中で失敗したら後続を実行しない。meter が不確か、または競合が見られる場合は追加の復元書込みを避け、status 9 として隔離する。復元を保証できるのは読み戻しが成功した場合に限る。

CKGEN 7 と 24 は比較対象であり、26 MHz の基準発振器を独立に検証したものではない。計測完了、外部主体の排他、CPU 物理クロックの絶対値は証明しない。`cm=3` は生カウントを出力し、校正済み周波数と表示しない。ホスト模擬試験は `c++ -std=c++17 -O2 -Wall -Wextra -Werror scripts/tests/test_cpu_meter_crosscheck.cpp -o /tmp/a04-crosscheck-test && /tmp/a04-crosscheck-test`。実機では未検証。

`cm=3` は測定中または前後に周辺状態の変化を検出すると、個別結果の status 7 を保持しつつ全体を status 9 とし、それ以降のルート復元書込みを行わず隔離する。復元が部分的に進んだ後に変化を検出した場合も、残りの書込みを止める。watchdog の給餌は、書込み前のロック取得失敗・事前条件拒否（`route_stage=0` かつ status 1 または 2）、または meter と両ルートの復元が読み戻しで確認された `route_stage=4` の場合に、ログ出力より先に戻す。最初の書込み後の読み戻し失敗も `route_stage=0` になり得るが status 9 なので給餌停止を維持する。読み戻しとローカルロックは外部主体との競合がなかったことを保証しない。

`cm=2` の従来動作は変更していない。周辺状態変化の status 7 でも、既存の所有状態チェックを通ればルート復元を試み、復元後は給餌を再開する。
