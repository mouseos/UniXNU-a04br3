# Pipeline・PCOの時間診断

診断用Mesaは `scripts/data/pvr-mesa-inputs.json` のcommitとtreeで固定する。通常は無効で、対象Metal bundleのビルド時に `A04BR3_POWERVR_PIPELINE_TIMING=1` を指定した場合のみ、bundleのconstructorが `PVR_PIPELINE_TIMING=1` を設定する。probeとMetalが必要。shader captureやSIPF1 dumpは有効化しない。

ユーザー空間の `CLOCK_MONOTONIC` ナノ秒を使用し、graphicsとcompute pipeline作成のうち64回ごとに最大8件、PCO IR処理のうち64回ごとに最大4件をprocessごとに記録する。限度に達すると以降は記録しない。clockの取得失敗や逆行は出力しない。pipelineのcache状態は未実装のため `cache=?` と記録する。PCO passは各実行回数とinclusive経過時間を集計する。入れ子passの値を加算して総時間としない。pipeline時間には開始行や内部PCO診断の同期出力コストが含まれ得る。

`TIM` にpidと `kern.boottime` を出し、取得できない場合は `boot=unknown` とする。`PL` はpid、ordinal、graphics/compute、start/end、結果を示す。`PCO` は開始・終了時刻と各passの回数・累計を示す。pass行のpid、ordinal、回数、時間は16進数で、時間の単位はns。長い行は同一pid・ordinal・indexの `PCN` / `PCV` ２行に分ける。記録は同時に取得されたsnapshotではない。時刻とカーネルログの関係は対象上で別途確認する。

出力は既存Metal bundleの `native_metal_board_text` を動的に探し、そのUART marker経路を使う。見つからない場合はstderrへフォールバックする。各行の送信は同期であり、診断が性能を乱す可能性がある。対象でsinkの解決、行の完全性、clock基準と実負荷を確認するまで観測成功とは扱わない。

ホストではMesa内 `src/imagination/pco/tests/test_pco_timing.py`、`src/imagination/vulkan/tests/test_pvr_timing.py` とdevice内 `scripts/tests/test_pipeline_timing_switch.py` を実行する。Xcode・iOS SDK・実機でのビルドと動作は別に検証する。
