# UI遅延の時刻相関診断

既定オフ。CPU・メモリ診断のBoard／BootProcessコードには変更を加えない。
実機への適用は主担当がcombined diagnosticのソース固定後に行う。

- kernel: `native-ui-latency=1`。`a04br3-input=1`が必要。
- plugin: 既存build profileへ`--ui-latency`だけ追加。
- NativeMobileDisplayのobserver変更、device Input変更、NativeMetal変更を同時に固定する。
  弱参照先がない旧display archiveでは入力だけ記録されるため、較正・全経路解析は成立しない。
- `python3 scripts/analyze-a04br3-ui-latency.py FIRST_BOOT.txt > timeline.json`
  `Darwin Kernel Version`が1件だけの初回epochを使う。通常readbackや端末操作は不要。

`UIK clock`と`UIL ... clock`はtimebaseを示す。時刻・IDは16進。
UIK 1/2は接触／解放のpublish入口、3/4は対応する同期HID sink復帰。
これはADC取得／IRQ時刻やbackboarddによる入力消費時刻ではない。
UIK 30は受理されたoffer、31はsource mapping引渡し、32は有効な完了要求受理、
33は同期legacy完了処理からの復帰。completedカウンタ増加とは区別する。
UIL 10/12/11はrender-pass commandのcommit入口／正常submit復帰／runtime callback。
20/22/21は非render command。41はWAIT_SWAP復帰、42/43はCOMPLETE_SWAP呼出し前後。
render callbackも視覚的なフレーム表示の証明ではない。

user 42/43の間にkernel 32が実行されることを利用し、同じswap IDから
kernel−user時計offsetの上下限を求める。両timebase一致と較正サンプルがそろうまで
input→renderの異なる時計間の差を算出しない。各側の同一操作区間は別に計算する。
較正後もinputとrenderのIDは別であり、次のcommitとの時間的近接を因果関係と断定しない。
UART出力順ではなく記録tickを参照し、`lost`のある区間では欠落を明示する。

固定256件try-lock queueを各1秒の観測threadが最大256件排出する。
競合／満杯で待たずに欠落カウンタを増やす。hot pathでformat／UART／allocationを行わない。
カーネル側observerは常駐boardコードだけを一度登録し、unloadや置換を行わない。
高負荷時の診断出力自体は観測を乱すため、定常性能の比較測定とは分ける。

30秒の原因は未確定。まず入力→HID sink、render commit呼出し、commit→callback、
offer待ち、mapping、copy／診断／ユーザー実行、legacy完了のどこに時間があるかを分離する。
CPU・メモリ担当の同一epoch／同時刻の観測と照合する。
