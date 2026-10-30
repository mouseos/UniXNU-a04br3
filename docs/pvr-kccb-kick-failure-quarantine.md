# KCCB KICK失敗時の隔離候補

基準は機種source `f2dee958a180ae6fee7b84a44baf9d74dcd22aff`。
短時間poll候補は含めない。既存実装はCCCB書込み後にKCCBを送るが、
Linux由来のvoid shimは送信エラーをログだけで捨て、submitは成功fenceを返す。
Linux側は送信前にKCCB slotを予約しており、この前提は現行shimにない。

KICKのring-full `-EBUSY` は書込み前の検出なので従来通り有界に再試行する。
timeoutはenqueue後で、FWが遅れて実行する可能性があるため再送しない。
送信失敗はジョブ層へ伝え、以降のkickを止め、バッチの全fenceを失敗として
記録してsubmitを `-EIO` にする。実際にCCCBへ書いた各ジョブと組のfragmentは
失敗・隔離とし、contextをfaultyにしてジョブ参照を再起動まで保持する。
送信済みか不明なジョブも同様に扱う。未着手のジョブだけは解放する。
VMのmap/unmapと共通のVM lockで該当VMを固定し、追加参照を一度取り、以降の
map/unmapとそのVMに対する新規submitを拒否する。close後もページテーブル・
マッピング・BOとジョブが残る。後からUFOが進んでも隔離ジョブは退役しない。
保持は意図的なリークであり復旧は再起動。成功時の経路は従来通り。

ホストfixtureは実際のkick/retry/隔離関数と完了workerを使用し、正常、部分成功、
paired kick、未送信EBUSY、遅延完了し得るtimeout、重複隔離、UFO後の非退役、
未送信fence失敗の経路を検証する。ハードウェア・FWの停止や実機性能は検証しない。
呼出し元のVM操作との並行順序、他のFWCCB/cleanup経路の所有権、ユーザー側が
保持するメモリの変更、close/reopenや回復の実機動作は独立レビュー・統合検証が必要。
この候補の固定commitは実機投入の承認を意味しない。
