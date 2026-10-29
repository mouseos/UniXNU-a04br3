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
比較し、署名済kernel・Data・trust・GPU addonは両者で同一とする。乱数による
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
