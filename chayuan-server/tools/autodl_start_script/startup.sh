#!/bin/bash

rm -rf xinference-output.log
rm -rf chayuan-output.log

XINF_PID=xinference.pid
CHAYUAN_PID=chayuan.pid


echo "xinf stat"
kill `cat $XINF_PID`
rm -rf $XINF_PID

sleep 2
xinference_P_ID=`ps -ef | grep -w "xinference" | grep -v "grep" | awk '{print $2}'`
if [ "$xinference_P_ID" == "" ]; then
    echo "=== xinference process not exists or stop success"
else
    echo "=== xinference process pid is:$xinference_P_ID"
    echo "=== begin kill xinference process, pid is:$xinference_P_ID"
    kill -9 $xinference_P_ID
fi

echo "chayuan stat"
kill `cat $CHAYUAN_PID`
rm -rf $CHAYUAN_PID

sleep 1
chayuan_P_ID=`ps -ef | grep -w "chayuan" | grep -v "grep" | awk '{print $2}'`
if [ "$chayuan_P_ID" == "" ]; then
    echo "=== chayuan process not exists or stop success"
else
    echo "=== chayuan process pid is:$chayuan_P_ID"
    echo "=== begin kill chayuan process, pid is:$chayuan_P_ID"
    kill -9 $chayuan_P_ID
fi



bash /root/download_model.sh

bash /root/start_xinference.sh 

bash /root/start_chayuan.sh 



