# Pipeline・PCOの時間診断

診断用Mesaは `scripts/data/pvr-mesa-inputs.json` のcommitとtreeで固定する。通常は無効で、対象Metal bundleのビルド時に `A04BR3_POWERVR_PIPELINE_TIMING=1` を指定した場合のみ、bundleのconstructorが `PVR_PIPELINE_TIMING=1` を設定する。probeとMetalが必要。shader captureやSIPF1 dumpは有効化しない。

ユーザー空間の `CLOCK_MONOTONIC` ナノ秒を使用し、graphicsとcompute pipeline作成を合算で64回ごとに最大4件、各pipelineの同一スレッドで呼ばれたPCO IR処理を最大4件記録する。pipeline IDはprocess内で共有し、同じpipeline内のPCOには子番号を付ける。制限超過や計測失敗は `drop` / `err` に出す。各passの回数とinclusive時間を記録し、入れ子の値は重複するので加算しない。計測用時計と集計のオーバーヘッドは残る。

pipeline開始前に `PL ... pre` として通知し、送信が終わった後の時刻からpipelineを計測する。終了後に実測start/endとPCOの集計を送信する。再入したpipelineの通知と集計は、最外側の計測後に `dpre` として遅延出力する。これら自スレッドの送信は該当区間の外だが、別スレッドの同期UART出力、スケジューリングやclock・集計処理の影響は除去できない。`TIM` にpidと `kern.boottime` を出し、取得できない場合は `boot=unknown` とする。`PL ... pre` はpid・pipeline ID・OS thread IDを含み、そのIDで `PL`、`PCS`、`PCO` の行を対応付ける。`TIM` のPID・boot秒は10進数、それ以外のID、PID、thread ID、時刻、回数は16進数、時間はns。長いpass行は同一の親ID・子番号・pass番号で `PCN` / `PCV` に分ける。開始・終了の時計取得失敗やscope確保失敗は明示する。再入時に記録容量を超えたものは `nested_drop` に出す。

対象PVRのgraphicsとcomputeのpipeline compile関数では、渡されたpipeline cache引数を参照・検索していない。その事実のみを `pvr_cache=not_queried` として記録する。hitやmissは推測しない。他のfrontendや共通処理のキャッシュ状態を意味しない。`CLOCK_MONOTONIC` とカーネル側の `mach_absolute_time`・heartbeatの基準やsleep時の関係は同一と仮定せず、対象上で別途確認する。

出力は既存Metal bundleの `native_metal_board_text` を動的に探し、そのUART marker経路を使う。見つからない場合はstderrへフォールバックする。各行の送信は同期であり、診断が性能を乱す可能性がある。対象でsinkの解決、行の完全性、clock基準と実負荷を確認するまで観測成功とは扱わない。

ホストではMesa内 `src/imagination/pco/tests/test_pco_timing.py`、`src/imagination/vulkan/tests/test_pvr_timing.py` とdevice内 `scripts/tests/test_pipeline_timing_switch.py` を実行する。Xcode・iOS SDK・実機でのビルドと動作は別に検証する。

既存iostat4との比較では、過去の固定Data seed、６エントリのGPU addon、既存trustを使用する。`scripts/make-a04br3-pipeline-addon.py` は、macOS runnerで署名・bundle seal・CDHashを確認した診断bundleとその出自記録を受け取り、固定ハッシュを検査する。元の６エントリの順序とmodeを保ち、診断を組み込んだMetal実行ファイル・そのMesaライブラリ・resource sealのみ置き換える。Info.plistが異なる場合は停止する。元のtrustモジュールを維持して新しい署名２件を追加し、Data seedは変更しない。

カーネルは固定Data/Cache UUIDと既存のVM・GPU・MSDC条件を指定して別途増分ビルドする。bundleの署名確認と完成kernelはmacOSで検証する。固定baselineのData・GPU・trustを公式ソースだけで再生成できることや、既存persistent storeと実際に一致することは未確認である。
