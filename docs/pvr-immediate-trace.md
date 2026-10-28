# PowerVR 長時間イベントの即時記録

`a04br3-gio=0` または未指定では無効、`=1` は従来どおり、`=2` は従来の
2msリングと10秒レポートを維持しつつ、1秒以上の完了記録を同期的に即時出力する。
長名 `a04br3-gpu-io-trace` を明示した場合は短名より優先する。即時出力は
bootごと最大8件。枠を超えた件数は `skipped`、リングの欠落は従来の `dropped`。
同じ完了イベントが即時と周期レポートの両方に現れる。pid/tid/kind/start/endで照合する。

1秒以上の分類は変換された mach absolute ticks で行う。`start_ns` と `end_ns` は
`mach_absolute_time()` を `absolutetime_to_nanoseconds()` で変換したカーネル時計の値。
差 `end_ns - start_ns` はそのイベントの経過時間で、スケジューリング遅延を含む。
Mesa `QT`/`XT` の時刻は `CLOCK_MONOTONIC` の16進ナノ秒であり、差は同じ時刻基準と
仮定してよい根拠がない。両者を同一瞬間で対応付けた実測 `U_ref`（user）と
`K_ref`（kernel）が得られた場合だけ、イベントの kernel 時刻 `K_event` を
`K_event + (U_ref - K_ref)` に換算する。
校正の時間幅・符号・誤差を記録し、報告済み約33秒差を固定オフセットとして適用しない。
同一boot・pid・tid・イベント範囲を照合する。duration自体にepoch補正は不要。

IOLogは同期出力でCPUとUARTに負荷をかけ、呼出し側の実行時間を延ばす。出力は
完了した関数の記録後で、GPU submitのユーザーclient lockは解放後。枠の確保とIOLogは
非同期panicに対して不可分ではない。完了前panic、IOLog中panic、8件枠の先行消費、
出力遅延の場合は取りこぼし得るため、panic前の回収は保証しない。旧リング記録も
維持し、即時出力自体の所要・他スレッド遅延と照合する。
