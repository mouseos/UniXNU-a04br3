# GPU I/OのTID別集計

`a04br3-gio=6` は既定OFF。`=5` のfence理由・待機系列診断に加え、
backboarddのWaitFence、SubmitJobsのロック待ち、SubmitJobsの処理を
PID/TIDごとに集計する。成功2 ms未満も含む。値は種類ごとの件数、
累積経過ナノ秒、非ゼロ結果件数であり、CPU時間やGPU実行時間ではない。
同じ行の値は累積なので繰り返し加算しない。

監視スレッドは各巡の前に250 ms sleepし、変化があったTIDを最大2件
出力する。枠は16個、try-lockが競合した記録は破棄し、busyを増やす。
枠不足の記録はfullを増やす。busyには集計取得側の競合も含まれるため、
失った呼出しの数と必ずしも一致しない。出力時のbusy/fullが0でも、
その後panicまでの欠落や、回収後・出力前のpanicは否定できない。
行はロック内でコピーし、IOLogはロック外で行う。各行には対象TIDの
最初と最後の完了記録の時刻を添える。未完了I/Oは集計されない。

mode6では既存の完了I/Oリングへ投入しないため、そのリングのdroppedや
短時間集計はmode6の欠落・件数を表さない。1秒以上の既存immediate出力は
別途維持する。周期report、fence理由、active、系列の出力も別枠にあり、
liveの出力上限だけで診断全体のUART負荷は定まらない。fence理由と系列の
抑制カウンタは既存周期reportに依存し、`a04br3-proclog` 無効時は見えない。

集計対象はWaitFence入口のconnection lock前やSubmitJobs以外のmethod、
ユーザー空間の処理・待機を網羅しない。同一PID/TIDの再利用を区別できず、
捕捉時刻はユーザー空間へ制御が戻った時刻ではない。同期UARTでactive監視の
次の走査も遅れ、短く終わった待機を観測できない場合がある。診断は実行時間や
充電ownerに影響し得るため、既存記録と比較する。
