# backboardd の待機時系列

`a04br3-gio=3` は既定OFFの機種固有診断。従来の `=1` のリング・周期出力と
`=2` の単一1秒以上イベント即時出力を維持し、WaitFence の開始と、同一
pid/tid/fenceの完了待機の集計を加える。Mesaは変更しない。

最初の `wait begin` は待機関数の呼出し直前に出す。IOLog後に開始時刻を
取り直すので、記録した待機時間にbeginログの同期出力時間は入らない。
begin出力と実際の待機呼出しの間に停止すれば、実行開始を証明しない。
同じpid/tid/fenceについて、完了した待機の経過時間が累計1秒に達するたび
`wait series` を出す。sumは待機中のスケジューリング遅延を含み、待機間の
隙間とログ出力は含まない。count、非ゼロ戻りのerrors、最後のresultを記録。
非timeout戻りで系列を閉じ、beginが出た場合は1秒未満でも終了情報を出す。
timeoutはそのまま同じ系列を継続する。集計出力後の値は次の区間にリセット。

ロックは一度だけ取得を試み、競合時は待たず計数して欠測とする。16スロット、
終了した系列か5秒以上停止した非待機スロットを再利用。完了時のロック競合で
待機中状態が残る可能性があるため、65秒後は実際に待機中であっても追い出し、
`expired_pending` を記録する。beginとseriesはそれぞれ時間窓10秒につき
最大4件で、境界直前・直後には連続して出る場合がある。抑制・busy・full・
時刻逆転/未登録・evictedは周期レポートに記録。カウンタ自体もpanic前に
出ないことがある。出力自体は同期で、UARTとスケジューリングに影響する。
保存枠競合・有限枠・レート制限・出力中panic・完了前panicにより、全記録の
回収や未完了と断定できることは保証しない。

時刻とsumは `mach_absolute_time` を nanoseconds へ変換したkernel基準。
Mesa `QT`/`XT` は CLOCK_MONOTONIC の16進nanoseconds。独立した同時刻ペアを
`U_ref`（user）、`K_ref`（kernel）として取得できた場合のみ、kernel時刻
`K_event` を `K_event + (U_ref - K_ref)` に換算する。報告済み約33秒差は
符号・誤差・安定性未確認のため固定補正に使わない。各sumのdurationはepoch
補正不要。begin、series、従来リングはpid/tid/fenceと範囲で照合し、同じ
待機を二重に足さない。実機での回収・UART負荷は未検証。
