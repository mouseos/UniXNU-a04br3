# fence待機理由の観測

`a04br3-gio=5` は既定オフ。`=4` の記録に加え、backboarddの50ms以上の
WaitFenceを、開始・終了と同じ `mach_absolute_time` のナノ秒で記録する。
報告は１秒の固定窓で全体２件、同じ接続・pid・tid・fenceは500ms以上
空ける。短い待機は枠を使わない。固定窓の境界では短時間に連続し得る。
最大16件のキーを持ち、上限時は古いキーを置換する。`limit_busy`、
`limit_suppressed`、`limit_evicted` は累積であり、出力されなかった待機の
存在を否定できない。診断ロックはtry-lockで、IOLogは既存ロックを
解放した後に行う。IOLog、追加のreadと時計取得は動作時間に影響し得る。

`sample_valid=0` は両ロック取得後の情報が取れていないという意味で、
ゼロという観測値ではない。`sample_ns` は最後に両ロックを取得した時刻。
`found=0` の場合、job/ctx/type/seq/ufoは有効ではない。`found=1` では
対象jobのseqと読み取ったufoを示す。FWCCBのread/writeとactive、readyも
同じロック区間に得た最後の標本だが、firmwareは並行して書き込むため
これら全体は原子的なsnapshotではない。`late_pending` は最後の標本内で
成功直前に保留が判明したことを示す。`fw_lock_fail`、`jobs_lock_fail`、
`fw_pending`、`ufo_pending` はこの１回のWaitFenceで観測した反復数で、
時間やCPU利用率ではない。UFO値は実際の判定でも再度読み取るため、
標本から判定までの値変化はあり得る。

UFO未到達、FWCCB保留、ロック競合を切り分ける材料であり、いずれか一つの
標本だけで遅延原因やハングを断定しない。実機での動作・充電への影響は未検証。
