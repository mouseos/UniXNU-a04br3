# 署名準備 read-batch の比較プロファイル

`scripts/a04br3-read-batch-profiles.py` は保存済み boot manifest の
`kernel_args` から2種類の引数を生成する。両方で `native-root-timing=1` を
有効にし、バッチ側だけに既存の `native-cs-read-batch=1` を加える。
`native-cs-coalesce` は指定しない。元の実験で使用した診断引数
`ecb=1`、`mb=2`、`a04br3-gio=6`、`native-io-trace=1` は両方から除く。
`ecb` と `mb` はメモリーコピー診断、`a04br3-gio` はGPU I/O診断、
`native-io-trace` はI/O重複の記録である。したがって2種類の間はバッチの
有無だけが異なるが、元の io-overlap-7fb 起動とは同一条件ではない。

保存済みmanifestでは通常側999 bytes、バッチ側1022 bytesで、後者は
終端NUL込み1023 bytes。タイミング用フラグを有効にしたカーネルが別途必要。
`RST hash` の `rc`、`rb`、`rn`、`on`、`dn`、`bat` と、実測区間の完了・
panic・再起動を比較する。診断の計時負荷、キャッシュ状態、ブート順序、
未完了区間は結果の解釈に含める。異なるboot間の集計値は同一処理量を保証
しない。比較には同一固定ソース・payload・初期条件を使う。

```sh
python3 scripts/a04br3-read-batch-profiles.py /path/to/manifest.json
python3 scripts/tests/test_read_batch_profiles.py
```

スクリプトは引数文字列を出力するだけで、bootを生成・書込みしない。
改ざん拒否のhost試験はXNU側の `check_native4k_page_validation.py` と
`check_root_read_batch.py` を使用する。実機性能や実機安全性は未検証。
