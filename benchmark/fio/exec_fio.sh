#!/bin/bash

histdir="results/exp-01"
#devlist=`lsblk | grep disk | grep -v sdat | awk '{print $1}' | grep -v nvme`
devlist="nvme0n1 nvme1n1"
rw=$1
bs=$2
readratio=$3
iodepth=$4

fname=mra-$rw-$bs-$readratio-$iodepth.fio
logfname="$histdir/fio-summary.log"
fiolog=log-$rw-$bs-$readratio-$iodepth.txt

mkdir -p $histdir

echo "[global]" > $fname
echo "ioengine=libaio" >> $fname
echo "size=400G" >> $fname
echo "rw=$rw" >> $fname
echo "bs=$bs" >> $fname
echo "direct=1" >> $fname
echo "iodepth=$iodepth" >> $fname
echo "ramp_time=5" >> $fname
echo "runtime=300" >> $fname
echo "invalidate=1" >> $fname
echo "rwmixread=$readratio" >> $fname
echo "invalidate=1" >> $fname
echo "" >> $fname

for i in $devlist
do
	echo "[job-$i]" >> $fname
	echo "filename=/dev/$i" >> $fname
	echo "" >> $fname
done

sudo fio --output=$fiolog $fname
sudo su -c 'echo 3 > /proc/sys/vm/drop_caches'

echo "rw=$rw bs=$bs readratio=$readratio iodepth=$iodepth" >> $logfname
./parse_and_report_influxdb.sh $fiolog $bs >> $logfname

sudo mv *.fio $histdir
sudo mv *.txt $histdir

