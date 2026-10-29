# read-batch比較用UART記録

`scripts/collect-read-batch-uart.py` は既に別プロセスが収集しているUART
ファイルと、書込み担当が作るflash記録を読むだけである。ポートを開かず、
端末の書込み・再起動・USB照会をしない。

書込み前にUARTファイルだけからチェックポイントを作る。書込みや再起動は
担当者が別途行う。

```sh
python3 scripts/collect-read-batch-uart.py --uart-log /path/to/existing-uart.log \
  --checkpoint --out /path/to/new-checkpoint.json
```

flash記録はチェックポイントからの `uart_offset`（書込み前に確認した同一UARTファイルのバイト位置）、
`boot_sha256`（書き込むbootファイルのSHA-256）を持つJSON。書込み担当が
正常終了の証拠を確認した場合だけ `flash_success: true` と、ログや記録の
参照を表す非空の `flash_success_evidence`、そのチェックポイントへの
`uart_checkpoint`（絶対またはflash JSONからの相対パス）と、正常終了時に採取した同一UART
ファイルの `flash_completed_uart_offset` を追加する。最初のpreloaderがこの
終了offset以降にあることも比較条件とする。これらは担当者の
申告であり、本ツールが実機の書込みや証拠の内容を検証するわけではない。
未記入でも保存できるが、比較適格とはしない。

```sh
python3 scripts/collect-read-batch-uart.py \
  --uart-log /path/to/existing-uart.log \
  --flash-record /path/to/flash.json --out /path/to/new-capture
```

collectorはcheckpointのinodeとoffset直前の最大4096 bytesのhashを再確認する。
未指定の場合も記録できるが比較対象外とする。同一inodeの古い箇所の改変は
証明できない。実行時に確定したファイル末尾までを `post-flash.raw` にコピーし、初回と
後続の `[pmic6392_init] Preloader Start` ごとに `boot-N.raw` を保存する。
`capture.json` は元のflash JSONとcheckpoint・直前bytesのコピー、各範囲の絶対
バイト位置、SHA-256、
BOOT_REASON、panic候補、EL3例外候補と次preloaderによる境界の有無を記録する。
flash offset後、最初のpreloader以前のprefixも別々に記録する。次回起動理由を
先行bootの `subsequent_boot_reason_candidate` として出すが、panicがその原因とは
断定しない。検出イベントの `offset` は各区間内の相対位置、`absolute_offset` は
UARTファイルの絶対位置。旧bootや次回bootのpanicを初回bootへ移さない。末尾bootは未閉鎖と
明記する。BOOT_REASONはそのbootの開始理由であり、直前panicの原因を自動確定
しない。UARTロス、同一サイズの並行上書き、自己申告である `flash_success_claimed`、観測の完全性はこの
snapshotだけでは証明できない。別ファイルへの切替・短縮・offset範囲外・
最大サイズ超過は失敗し、出力を上書きしない。既定上限128 MiBは明示的な
`--max-bytes` で変更できる。

共通解析器 `UniXNU/scripts/analyze-read-batch.py` はこの記録とペアmanifestを
読み、最初のbootのみを比較する。詳細はUniXNU側ドキュメントを参照。

最初のpreloaderが未観測でも `post-flash.raw` とprefixの異常候補を保存する。
完了offsetがprefix内にあれば書込み完了の前後の候補を分け、範囲外なら境界不明とする。
終了までの旧boot、初回boot、後続bootの区別が確定できない場合は比較できない。
`--max-bytes` はsnapshotのバイト数上限で、処理中にコピーが生じるためプロセスの
実メモリ上限ではない。出力先は原子的に予約し、途中失敗時は不完全な出力が残り得る。
パターン検出は全種類の例外・watchdogを網羅しない。BOOT_REASONと外部記録を併用する。

Linuxの通常ファイルのみを安全に開き、FIFOやデバイスを拒否する。checkpointとflashの
JSONも通常ファイルを使い、各1 MiBを上限とする。JSONの重複キーは拒否する。
BOOT_REASONは改行で閉じた短い数値のみ認識し、未完・不正のマーカーは数だけ残す。
bootは4096件、イベントは全区間の記録合計4096件で打ち切りを明記する。
prefixの全体と完了前後の内訳など、同一イベントの複数回記録も合計に数える。生snapshotは全範囲を
残し、共通解析器は打ち切りを検出して停止する。capture.jsonは公開時に最後に移動する。
flash offsetがpreloaderの途中であることをcheckpoint直前のbytesで検出した場合は
`offset_crosses_preloader_anchor` を記録し、比較対象から除外する。元のUART受信が
欠損している場合、実際の初回を確認できない限界は残る。
