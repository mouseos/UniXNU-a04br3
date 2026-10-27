# A04BR3 GPU clock比較

この機種ではboot引数 `a04br3-gpu-clock` がない場合または0の場合、GPUのparent 4（26 MHz）を選ぶ。`a04br3-gpu-clock=380` を明示すると、既存のMMPLLとVCORE設定を読み、起動中・電圧範囲・周波数範囲・再読一致を検査した場合にparent 7を選ぶ。PLLとPMICの設定変更はしない。条件を満たさない場合は26 MHzに戻る。

`python3 scripts/a04br3-gpu-clock-profile.py --mhz 380 --args-file <baseline-args.txt>` は、実際に使用する全boot引数から明示380の比較用引数を生成する。26は既存または明示の時計引数を削除して `a04br3-gpu-clock=0` を付ける。CPU描画 `offline-legacy-cpu-present=1` とGPU有効化が欠けた入力、重複する時計引数、DarwinBootの1023 byte上限を超える結果は拒否する。既存GPU、Data、kernel、UEFIは変更しない。出力は標準出力にJSONで、`kernel_args` と `extra_kernel_arg` を含む。公式payload生成には `--kernel-arg=a04br3-gpu-clock=380` を渡す。

P28では380 MHzで描画診断のreadbackが有効な別build中にCommandWake timeoutとpanicがあり、原因は未確定。比較は画素readbackを加えず、同一kernel、GPU addon、Data、UEFIで26と380を分け、GPU正常完了、画面、touch、panic、同時負荷を検証する。計算されたparent周波数は実測値ではない。現在の温度・電源管理および長時間の安全性は未検証であり、380を本機の既定にはしない。
