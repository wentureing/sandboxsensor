#!/system/bin/sh
# 采样器 v3：由外层 shell 串行调用，输出到 stdout
OUT=/data/local/tmp/sample.txt
WINOUT=/data/local/tmp/winsnap.txt
rm -f $OUT $WINOUT

echo "START $(date +%H:%M:%S.%N | cut -c1-12)"
i=0
while [ $i -lt 60 ]; do
  TS=$(date +%H:%M:%S.%N | cut -c1-12)
  PID=$(pidof mark.via 2>/dev/null)
  FG=$(dumpsys activity activities 2>/dev/null | grep topResumedActivity | sed 's/.*u0 //; s/ .*//')
  echo "$TS i=$i pid=${PID:-none} fg=$FG" >> $OUT
  MOD=$((i % 3))
  if [ "$MOD" = "0" ]; then
    echo "===== SNAP $TS i=$i pid=${PID:-none} =====" >> $WINOUT
    dumpsys window windows 2>/dev/null | grep -E "^  Window #|mViewVisibility|mark\.via|nextvm" >> $WINOUT
  fi
  i=$((i+1))
  sleep 0.4
done
echo "DONE $(date +%H:%M:%S)" >> $OUT
