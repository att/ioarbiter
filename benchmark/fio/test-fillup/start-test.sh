#!/bin/bash

sudo time fio fillup-1.fio
sudo time fio fillup-2.fio
sudo time fio fillup-3.fio
sudo time fio fillup-4.fio
sudo time rm ../ssd50tb/iotest-1.bin
sudo time rm ../ssd50tb/iotest-2.bin
sudo time rm ../ssd50tb/iotest-3.bin
sudo time rm ../ssd50tb/iotest-4.bin

