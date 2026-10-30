# GPU lifecycleホストfixtureの固定入力と限界

`scripts/tests/test_powervr_gpu_lifecycle.py` は次を環境変数で明示し、全て完全な
40桁のcommitを要求する。実ソースはgit objectから読み、worktreeの未commit変更や
NativeMobileDisplayの現在HEADを混入させない。base commitの実gitlinkと指定された
NativeMobileDisplay commitの一致も検査し、入力SHA-256とcommitを標準出力に記録する。
全6項目が環境に存在しないときは通常のunittest探索で明示的にskipする。
空文字の指定や一部だけの指定、
不正なcommitやgitlink不一致は失敗として扱い、HEADを自動選択しない。

- `A04BR3_DEVICE_ROOT`、`A04BR3_DEVICE_COMMIT`: 検査するdevice repoとcommit。
- `UNIXNU_BASE_ROOT`、`UNIXNU_BASE_COMMIT`: UniXNU base repoとcommit。
- `NATIVE_MOBILE_DISPLAY_REPO`、`NATIVE_MOBILE_DISPLAY_COMMIT`: NativeMobileDisplay repoとcommit。

確認対象のbase `22146ccf1721db40f53e35f1afb1d745819f4735` のgitlinkは
`279cf9bd8e8b1751dcf991f46176dcc0c63d325d`。別作業treeのHEAD `5c75f253...` は
別commitであり、比較結果に使わない。固定したヘッダーからread-only flagを読み、
bit 63をコンパイル時に確認する。`KERN_PROTECTION_FAILURE=2`、`KERN_FAILURE=5`
を区別し、前者のみread-only retryが成功する場合と、両方の失敗経路を検査する。
XNU固定参照は `d8b80295118ef25ac3a784134bcf95cd8e88109f` の
`osfmk/mach/kern_return.h:76,91`。

`scripts/tests/test_powervr_lifecycle_pins.py` は上記の正しい環境を設定してから実行し、
commit未指定・短縮を確認し、独立した一時Git repo内の2 commitとgitlinkで
一致・不一致の検査を再現する。空文字単独指定も拒否する。full device pinを変更して各候補を
再試験し、出力を検証記録に残す。`git show` はローカルobjectを要求し、fetchしない。

各ケースは実関数を抽出するが、host descriptor、実BOのprepare/complete、cache、
GPU/firmware、通知の競合、process境界、IRQ、DMAおよび資源の実寿命はmockである。
ARM64はcache関数のコンパイル検査であり、実SDK全体のkernel linkやデバイス実行では
ない。成功は共有BOの再利用安全性、FW停止、close時の実機進行、P1解消を証明しない。

一時Gitリポジトリでは継承した `GIT_*` を除き、fixture専用の公開noreply作者・コミッターを環境に明示する。システム・グローバルGit設定とテンプレートを無効化し、各commit直前の実効識別子および生成後の作者・コミッターを検証する。外部の作者・コミッター・設定注入を模した負例も実行する。

固定入力の読み取り側とfixtureの子プロセスも外来 `GIT_*` を継承せず、指定repoとcommitを使用する。別のGitディレクトリ、worktree、index、object directory、設定注入を模した負例で正しいgitlinkの成功と不一致の拒否を確認する。
