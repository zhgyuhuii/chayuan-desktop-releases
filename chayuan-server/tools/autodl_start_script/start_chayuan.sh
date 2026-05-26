#!/bin/bash

conda run -n chayuan --no-capture-output export CHAYUAN_ROOT=/root/chayuan-data && chayuan start -a > >(tee chayuan-output.log) 2>&1 &
PID=$!
echo "Started chayuan with PID $PID"

echo "Checking if output.log has content..."
while [ ! -s chayuan-output.log ]; do
  echo "Waiting for output to appear in chayuan-output.log..."
  sleep 1
done
while ! grep -q "URL: http://0.0.0.0:6006" chayuan-output.log; do
        sleep 1
done


echo $PID > chayuan.pid
echo "chayuan started"

