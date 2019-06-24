#!/bin/bash

bslist="4k 8k 16k 32k 64k"
readratiolist="0 10 20 30 40 50 60 70 80 90 100"
iodepthlist="1 8 16 32 64" 

# random test
for bs in $bslist
do
	for readratio in $readratiolist
	do
		for iodepth in $iodepthlist
		do
			./exec_fio.sh randrw $bs $readratio $iodepth
		done
	done
done

bslist="128k 256k 512k 1024k 2048k 4096k"

# sequential test
for bs in $bslist
do
	for readratio in $readratiolist
	do
		for iodepth in $iodepthlist
		do
			./exec_fio.sh rw $bs $readratio $iodepth
		done
	done
done





