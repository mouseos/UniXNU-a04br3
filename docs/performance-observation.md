# 速度・使用量の診断経路

この追加は明示実行用user-space TCP測定とofflineログ解析のみ。
kernel、PMU、PLL、PMIC、既存memory/MSDC/GPU workerを変更しない。
ブート時の自動実行、接続、インストール、書込みは行わない。

## 既存測定の再利用

- CPU実クロック: ArmCpuのPMCCNTRと同一CPUのCNTVCT差分を使用。
  `cycles * CNTFRQ / ticks / 1e6`がMHz。CNTFRQだけをCPUクロックと呼ばない。
  起動時R5のsourceはArmCpu `b46fd9b099b87c3f22aa2dda77e27951efb1d9c2`、device
  `7a94b1e2f262bf472f4dac4d43bfa57646110998`。
  runtime R8はArmCpu `875e55c7ea2eb91114d6de277cd6f0674c14bf4d`、device
  `1d6da9debc0bad625b52e00c2d2ee9870c2f7f63`、base
  `ae90aa8401948ed029f5b7a03fca43e5929fb59b`。
  `armcpu-runtime-cycle=1`は機種限定・既定offの診断。CONFIG_CPU_COUNTERS=0の仮定、
  上位EL/debug所有未確認、約0.9ms busy区間、bind欠損を必ず添える。
  他imageへの無条件移植やPMU強制再設定、idle MHzの推定は行わない。
- CPU使用量: A04BR3CpuLoad.cの`cpu-load`累積user/system/idle/nice ticksの差分。
  非原子スナップショットであり、タスクの待機理由とは別。
- メモリ: BootProcessのVM pagesと既存memory-copy-benchのVM activity／copy検証。
  正本の小容量測定・圧迫回避付きdevice `172d6d346e250986329ca2b59549e872353028d2`を参照。
  空き不足skipは帯域0ではなく未測定。既存guardを外してallocationしない。
  free/speculative等は重なる可能性があり、カテゴリを足して物理使用量にしない。
- eMMC: NativeMsdc transportのmode/size別ok_bytes、elapsed_ns、errorsとqueue wait。
  9転送行を含む完結した`io sample ... io end`のみ解析。
  rateは累積service byte/timeで、bus最大帯域やwall-clock throughputではない。
  キャッシュ／検証／DMA／PIOの経路を混同しない。既存msdc-read-benchは通常ファイル範囲比較用。
- GPU: 既存owner rows probeの全payload/padding/source検証＋record/submit/wait/validate計時とPF。
  PFはCPU import/encode/commit/waitを含む経過時間。hardware GPU utilizationは未取得であり、
  frame頻度やPF durationから使用率を作らない。GPU probeとUIを同時実行したと推測しない。

`python3 scripts/a04br3-performance-report.py FIRST_BOOT.txt --epoch 1 > report.json`

全入力SHA、epoch byte区間、epoch SHAを保存する。次bootとはcounter差分を取らず、
reset/減少する32bit CPU ticksはunknown。複数CPUを同時刻の原子値とも扱わない。
CPU owner/windowのraw行を保持する。出自manifestと一緒に保管する。

## ネットワークの不足分

```
cc -O2 -std=c11 -Wall -Wextra -Werror tools/a04br3-network-bench.c -o /tmp/a04br3-network-bench
/tmp/a04br3-network-bench --serve HOST_INTERFACE_IP 49004
# 実機担当が署名owner binaryの既存実行経路で明示実行:
a04br3-network-bench --snapshot
a04br3-network-bench --run NUMERIC_HOST_IPV4 49004 1048576
```

C serverは明示bindの1接続のみ。Python serverはプロトコル試験の参照実装で、速度の測定にはC serverを使う。
両端の生成・hash・検証時間も別行に出し、endpoint CPU律速とネットワークを混同しない。
clientはDNSなし、1..16MiB各方向、接続を含め全体30秒のdeadline。
uploadはserverの全バイト検証ACKまで計時、downloadはclientの全バイト検証を含む。
最後のACKをserverが検証し、確認応答を返して初めてclientがverified=trueで成功する。
checksumは故障検出用FNV32で暗号学的認証ではない。信頼する試験用ネットワークで使用する。
radio/link speedではなくCPU検証・RTT込みのTCP application goodput。
getifaddrsのinterface別bytes/packetsを前後に出すが、他プロセスの通信も含む累積値。
32bit wrap/resetは別途unknown扱い。Wi-Fiへの関連付け・IP取得・server到達は別の前提条件。
既定引数なしではusageを返すだけで接続しない。失敗・途中EOF・破損・timeoutでは成功を出さない。
`--snapshot`でcounterを取得できなければ失敗扱い。

```
python3 scripts/build-a04br3-network-bench.py --out NEW_OUTPUT_DIR
```

既存macOS runnerのiPhoneOS SDKでCだけ増分buildしad-hoc署名する。
manifestにsource commit/hash、SDK、実コマンド、binary hash、CDHashを保存する。
既存owner trust/addonへの追加・起動経路・実行引数は実機担当が固定する。
このbuilderはtrustやDataを変更せず、新しいplistを置くだけでlaunchdから起動できるとも主張しない。

Host試験:
`python3 scripts/tests/test_performance_observation.py`
CPU clock／epoch／reset／不完全MSDCと、実localhost TCPの全検証／破損／EOF／deadlineを試す。
localhost成功は実機ネットワーク成功ではない。
