# SubmitJobs 時間の診断

`a04br3-gpu-stage=1` と `a04br3-gio=6` の両方を指定した場合だけ有効。既定では無効。既存の mode 6 monitor が起動できなければ出力されない（既存の monitor unavailable ログで判別する）。端末への投入・再起動を指示する設定ではない。

backboardd の完了した `native_powervr_submit_jobs` ごとに PID/TID と経過時間を取る。各スレッドの累積値を約250msごとに最大2行出力する。`total` は job layer の開始から終了まで、`create` は job 作成、`pte` は任意 prephase、`lock` は jobs lock 獲得、`space` は queue の空き確認と再試行時の睡眠など（再ロック待ちを除く）、`relock` はその睡眠後の jobs lock 再取得待ち、`bo` は host 同期、`kick` は run_job 群、`kccb` はその中の CCCB kick 関数呼出し。`kccb` は `kick` の内側、各項目は `total` の内側なので単純加算しない。すべて脱スケジューリングを含む壁時間で、ファームウェア・GPUの実行時間ではない。`total` は UserClient 外周、入力コピー、出力コピーを含まない。`success` と `failed` は job layer の戻り値の分類で、`calls = success + failed`。処理開始前の引数検査で拒否された呼出しは含まれず、UserClient側の入力検証や非同期GPU実行の成功・失敗も表さない。`space` は睡眠と脱スケジューリング、確認処理を含み、queue が満杯だった実時間そのものではない。従来の全体集計 `gpu submit perf: stage=space` は従来どおり再ロックを含み、この `space` とは定義が異なる。

保存枠は16 PID/TID、再利用なし。上限到達、記録時や出力時の try-lock 衝突は `full`、`busy`、`report_busy` の累積欠落として別途出力する。未完了中の呼出しは含まれない。PID/TID 再利用は区別できず、first/last は一連の連続区間である証明にならない。結果は観測範囲と欠落を明記して既存 mode 6 の UserClient 外周およびユーザー側 tick と照合する。ログ出力や計測自体の影響は実機では未測定。測定開始・終了が診断有効時の job layer 内だけにあり、出力はロック外で行う。

ホスト検証: `g++ -std=c++17 -O1 -g -Wall -Wextra -Werror -fsanitize=undefined -pthread scripts/tests/test_pvr_submit_stage.cpp -o /tmp/pvr-submit-stage-test && /tmp/pvr-submit-stage-test`、`python3 scripts/tests/test_pvr_submit_stage_enable.py`、`python3 scripts/tests/test_pvr_submit_stage_paths.py`、`python3 scripts/tests/check_powervr_packet_evidence.py`。最後の検査は実 run_job に時刻 stub を渡し有効・無効両経路と既存 packet 機能を検査する。ホスト結果はカーネルビルドや端末動作の代用ではない。
