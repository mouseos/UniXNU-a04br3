# read-batch比較bootの生成準備

この準備用スクリプトは端末に接続しない。UniXNU `e8f5385e4cee02bb15efa2fb97c20bb59b98d72f`、
device `8fde8ba87486338342f3be528823cb46394f2c5b` を生成対象とし、
UEFIは正本 `uefi-a04br3` の `d3d4f28a89b29553ede7d7523c6af86d7e2459e8` を使う。
この文書と生成補助はdeviceの上記commitから分岐した準備ブランチにある。

1. 公式の `unixnu defconfig`、`unixnu fetch`、deviceの `a04br3-pre-build.sh`、
   `unixnu build kernel` で、新規のIPSW由来Data seedとROOT_TIMING有効kernelを
   用意する。前回の端末Data、実機から抽出したADTや識別値を使用しない。
   GPU addonとowner trustは固定deviceの `make-a04br3-gpu-addon.py` で、同じ
   pre-buildが生成・署名したprobeとMetal bundle、およびIPSWから生成した
   trustを使って作る。ログとsource・出力hash・Dataのvolume UUIDを記録する。
2. 既存d3d4 FDのUniXNU出自は `7fb02c6` で、比較kernelの `e8f5385` ではない。
両commitはdocsとhost testだけが異なり、DarwinBoot gitlinkは同一であることを
生成時に検査し、FDの実際の出自をそのまま記録する。

正本UEFIの `Platforms/Benesse/a04br3Pkg/scripts/build-recorded-fd.py` が
   内部で正規 `build_uefi.py` を呼ぶ。fixed UniXNU checkoutとともに実行し、
   出力FD・manifest・実際のMu_Basecoreのパッチ適用状態を照合する。
3. `a04br3-read-batch-record-inputs.py` は上記の入力とログのhashをまとめ、
   `inputs.json` と `data-provenance.json` を作る。両記録の内容は自己申告と
   hash照合であり、公式pre-buildの実行やIPSW由来の独立証明ではない。
   そのためログとrunnerの実行記録を独立に監査するまで、入力を適格と扱わない。
4. `a04br3-read-batch-build.py` は固定commit、入力hash、kernel側のPMGR/display
   report、UEFIの記録されたパッチtree、Data seedの実際のAPFS UUIDとkernel
   側UUIDを照合する。公式 `unixnu build payload` と `unixnu bootimg`、deviceの
   boot形式検証を使う。固定checkoutを変更する手パッチや実機コマンドは使わない。
   バリアントごとに別のwork treeを用い、payload/ADT/bootとログのhashを記録する。

この生成補助は、組ごとに端末に由来しない新規ソフトウェア識別値を生成する。
既存Dataやシステムの識別と一致する保証はないため、試験結果はこの構成に限定し、
配布物にしない。生成値は組のローカル `software-identity.txt` に0600で保存。
ADTは公式処理で各回新しい `random-seed` を生成する。両ADTはその乱数以外を
比較し、署名を前提とするkernel・Data・trust・GPU addonは両者で同一とする。
生成器はそれらの署名の有効性を検証しない。乱数による
起動時挙動への影響は排除できない。したがってbootはビット単位で再現しないし、
単一変数の厳密な実機比較も証明しない。乱数を固定したり生成ADTを手修正したり
して見かけの一致を作らない。計測と採否条件は `read-batch-matched-profile.md`。

ホストfixtureはpinned UniXNU CLIのparserとargument mergeを呼ぶが、payload・
bootimg実生成、macOS上のAPFS UUID検査、実機安全性を検証しない。`pair.json` と
`NOT_FOR_DISTRIBUTION.txt` は候補の範囲を明示する。基礎kernelとFDが両方とも
上記固定sourceから出たこと、およびpre-buildの実行とIPSW由来を独立に確認できる
までは、実機比較可能な完成bootとみなさない。fastboot書込みは親担当。

## 記録・生成コマンド

次の変数は公式pre-buildと固定source buildを実行した同一runner内の出力を
指す。seed、trust、GPU addonは端末から抽出しない。`PRE_BUILD_LOG` は実際の
pre-build全出力であり、記録生成器は空ログを拒否する。記録自体を独立監査
の代わりにはしない。

```sh
python3 scripts/a04br3-read-batch-record-inputs.py \
  --source "$UNIXNU_SOURCE" --device "$DEVICE_SOURCE" --uefi "$UEFI_SOURCE" \
  --kernel "$KERNEL" --data "$DATA_SEED" --trust "$OWNER_TRUST" --gpu "$GPU_ADDON" \
  --fd "$RECORDED_FD" --dtb "$UEFI_SOURCE/Resources/DTBs/a04br3.dtb" \
  --kernel-provenance "$KERNEL_RECORD" --firmware-provenance "$FIRMWARE_RECORD" \
  --seed-provenance "$SEED_RECORD" --pre-build-log "$PRE_BUILD_LOG" --out "$INPUT_RECORDS"

python3 scripts/a04br3-read-batch-build.py \
  --source "$UNIXNU_SOURCE" --device "$DEVICE_SOURCE" --uefi "$UEFI_SOURCE" \
  --inputs "$INPUT_RECORDS/inputs.json" --out "$PAIR_OUTPUT" --log-dir "$BUILD_LOG_DIR"
```

どちらも既存出力を上書きしない。build側は完成まで私有一時領域を使用し、
失敗時はその領域を消してログを残す。build済み検証済み固定sourceの
コンポーネントがない状態では通過させない。UIDはソフトウェア生成であるため、
実機由来のUIDを引数・環境変数・入力へ指定しない。現時点でこの手順は
macOS実環境で通し実行されておらず、準備コードとhost模擬のみである。

## 物理転送集計用の別ペア

`--measurement iostat` を上の `a04br3-read-batch-build.py` に追加すると、同じ
固定kernel・Data・FDと既存の実行時署名検証経路を使い、baseline/batch間の差がbatchフラグだけの
別ペアを生成する。引数長は署名計時ペアと同じ999/1022 bytes。両方で
`native-root-timing=1` の代わりに `native-msdc-iostat=1` を使用し、既存の
`a04br3-home-perf=1` と `pl=1` のreporter条件を必須にし、優先される
`a04br3-proclog` との併用を拒否する。未指定なら従来の
署名計時ペアを生成する。`pair.json` は `measurement` を記録する。生成元・
sourceの確認と、公式CLI、入力検証、boot形式検証の手順は共通である。

この別ペアではRSTの `bat` や署名準備bytes・時間を取得できない。取得できない
情報をゼロとみなさず、署名計時ペアの解析器に入力しない。署名計時ペアとiostat
ペアは診断負荷とADT乱数・起動条件が異なり、同一試行として直接対応付けられない。
後者は完成したUARTフレームを `summarize-msdc-io.py` で解析する。報告値は装置
全体の `transfer()` の累積値差分であり、PID・署名処理単独の値、媒体バス帯域、
キャッシュの実効や待機・CPU時間は証明しない。DMA値はコピー等も含み、失敗件数と
各前後サンプルの開始・終了時刻と非原子的な取得幅、ログ欠損を併記する。計測自体のCPU・UART負荷もある。
両条件の計測順、同一seedとキャッシュ状態、初回panicと再起動、対象処理の完了、
source・入力・boot hashを記録し、確認できなければ性能採否に使わない。

このモードは計測用bootの生成準備であり、この追加により実機性能や改ざん拒否が
証明されたわけではない。現行GPU試験中には端末操作をしない。

既存SSH runner上の前回固定入力を再利用する増分用に
`scripts/a04br3-read-batch-iostat-runner.sh` を用意する。新規専用task内へ
source/device/UEFI/preparationのcheckoutとsubmodule・Git領域を用意し、
前回記録の固定hashを照合して複写し、大きな入力もhashを確認した専用コピーへ
固定する。UUID・出自照合はそのコピーを使い、builderのstageでもhashを再照合する。
記録同梱と一致確認が済むまで完成名のpairを公開しない。`env -i` で子プロセスの環境を絞り、キャッシュ、
一時ファイルと出力先もtask内にする。旧checkoutで公式CLIを実行しない。
前回の公式pre-buildと記録が実行されたことはhashだけでは証明できないため、
既存の独立監査記録と併せて判断する。失敗または中断時のtaskは保持し、
同名taskの再利用はせずに原因と残存ファイル・マウントを確認する。
SIGKILLなどでは一時領域やmacOSの検証用マウントが残る可能性がある。

生成器は入力hashと出自記録の整合を確認するが、署名やtrust cacheの暗号学的な
有効性は検証しない。host改ざん試験も実機での改ざん拒否の証明ではない。
