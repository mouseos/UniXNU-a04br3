# 全タスク測定を外した比較用ビルド

`scripts/a04br3-iostat-stack-new-kernel.py` の `--all-task-control` は、同じ入力から作る `--all-task` の起動引数と比較して、末尾の `atp=1` のみを除く。GPU・Metal・`pl=60`・`a04br3-home-perf=1`・`native-msdc-iostat=1` を維持する。live PC、全タスク、meter、stream は同時に指定できない。既存モードの既定動作は変更しない。

この比較モードは device f2dee958a180ae6fee7b84a44baf9d74dcd22aff と記録済み kernel SHA-256 17904eca68b4c7e5f99350f4ddc57a671efc0357a93c984db9bb7f799336a4d1 を要求する。`--comparison-boot` と `--comparison-manifest` には既存 alltask 基準を明示する。基準 boot、基準 manifest、旧 pair は既知の SHA-256 に固定される。基準 boot の実FATファイル・引数と manifest を照合し、source・device・kernel record・FD・DTB・sidecar・Data UUID・ADT identity・seed・旧boot/pairを比較する。出力 manifest に基準 boot と基準 manifest のハッシュも記録する。

既存の f2 構築手順における `--all-task` を `--all-task-control --comparison-boot <基準boot.img> --comparison-manifest <基準manifest.json>` に置き換え、新しい出力ディレクトリを指定する。`--live-pc` は併用しない。旧 boot の実FAT設定は全モードで pair の引数と完全一致する必要があり、比較モードではさらに必須 GPU・自動復帰引数を要求する。公式 UniXNU CLI の payload / bootimg 生成、引数マージ、ADT、boot image validator の検査も維持する。既存成果物や ADT を手編集しない。

## 監査時の注意

- `atp=1` の差だけであることは実際に得た両 manifest、公式 CLI が生成した payload の引数と実機の boot-args でそれぞれ確認する。この修正のホスト試験は起動実績の証明ではない。
- 同一 Data image や OwnerData UUID は、永続 Data 内容、キャッシュの温度や外部負荷の一致を保証しない。交互順・複数回で条件と操作時点を記録する。
- 個々の boot image、ADT は差が生じる。identity と構造を照合し、random-seed 差を分離する。署名検証とソースからのビット再現性は別途必要。
- 全タスク測定を外すと、その測定自身の launchd CPU データは得られない。残した home-perf の既存カウンタと同じ単調時刻の窓を使う。各カウンタは非原子で、UART の欠損もありうる。
- GPU 引数の一致は GPU 描画の成功を意味しない。scanout、実画面・同じ操作の時間、panic / EL3・後続 boot reason を別に確認する。回復用イメージのハッシュ・端末識別を固定し、fastboot に復帰できない場合は回復未完了とする。
- 比較用の seed に端末固有データが含まれる場合は配布成果物にしない。配布用は端末固有値を起動時に実機から取得する別途の正式経路が必要。
