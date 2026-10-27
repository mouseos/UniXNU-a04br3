# CPU rail診断

`a04br3-cpu-rail=1` と `a04br3-proclog=<秒>` を両方指定すると、process-reportのkernel threadでMT6392 VPROCの設定selectorを読み取る。指定しなければ診断のマッピング・通信は実行しない。既定オフ。ログ `a04br3 cpu-rail stage=XNU` のbegin/endは同一起動のmach absolute timeから換算したナノ秒で、`nominal_uv`はselectorからの設定電圧であり、アナログ電圧の実測ではない。取得は非原子的で、前後の値が同じでも途中の変化や別のハードウェア所有者の操作は否定できない。途中失敗、lock競合、設定変化は数値を出さずunknownとする。

PWRAP readはWACS2のCMDとVLDCLRへの制御書込みを伴う。PMIC設定・PLL・電圧への書込みは行わない。入力VGP1、GPUのVCORE、RTC recoveryを含むこのdevice実装のWACS2操作は同じmutexで直列化する。lockはboard initで作り、割込み文脈または割込み禁止中は取得しない。診断はtry-lockで混雑時に待たない。送信後のtimeoutやINIT_DONE消失により応答が不確かな場合は残りのboot中WACS2を隔離するため、未起動のtouchやGPU等は利用不能になる場合がある。早期RTC失敗は既存watchdog/回復動作へ影響するため実機の初回検証が必要。panic時の再起動経路はWACS2やmutexを使わない。

上位ELや別driverなど、ここに含まれないWACS2利用者の不在は保証されていない。端末の同時利用者と、診断on/off両方で入力・GPU・recoveryが維持されることを実機で確認する。現状のtemperatureは未取得。thermal controllerの初期化・鮮度・校正を検証するまで数値を出さない。

UEFIのSEC/EBSはこのkernel mutexの対象外。各phaseの独立した所有権とtimeout後の処理を確認するまでは対応値を作らない。異なる起動の値を比較しない。

ホスト検証は `python3 scripts/tests/test_a04br3_wacs_lock.py`、`python3 scripts/tests/test_a04br3_cpu_rail.py`、`python3 scripts/tests/test_a04br3_register_lock.py`。これらはMMIOや実機での動作を証明しない。
