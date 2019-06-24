#!/bin/bash

if [[ -z $1 || ! -s $1 ]]; then
        echo "! no such file exists: $1"
        exit
fi

str=$2
i=$((${#str}-1))
unit="${str:$i:1}"
bs=`echo $2 | sed -e "s/[KkBb]$//"`
if [ "$unit" = "B" ] || [ "$unit" = "b" ]
then
        parsed=`echo "scale=3; $bs/1000" | bc`
        bs=`echo "0"$parsed`
fi

# parse iops
iotype="read"
readiops=`cat $1 | grep iops | grep $iotype | awk -F, '{print $3}' | awk -F= '{print $2}'`
if [ -z "$readiops" ]
then
        readiops=0
else
        newiops=0
        for riops in $readiops
        do
                #echo $riops
                newiops=`echo $newiops + $riops | bc`
        done
        readiops=$newiops
fi

iotype="write"
writeiops=`cat $1 | grep iops | grep $iotype | awk -F, '{print $3}' | awk -F= '{print $2}'`
if [ -z "$writeiops" ]
then
        writeiops=0
else
        newiops=0
        for wiops in $writeiops
        do
                #echo $wiops
                newiops=`echo $newiops + $wiops | bc`
        done
        writeiops=$newiops
fi

totaliops=$((readiops+writeiops))


# parse latency
latunit=`cat $1 | grep lat | grep -v "%" | grep -v percentile | grep -v slat | grep -v clat | awk -F\( '{print $2}' | awk -F\) '{print $1}'`
arrlatunit=($latunit)
latnums=`cat $1 | grep lat | grep -v "%" | grep -v percentile | grep -v slat | grep -v clat | awk -F, '{print $3}' | awk -F= '{print $2}'`
cnts=`cat $1 | grep lat | grep -v "%" | grep -v percentile | grep -v slat | grep -v clat | awk -F, '{print $3}' | awk -F= '{print $2}' | wc -l`

newlat=0
itr=0
for lat in $latnums
do
        #echo $lat ${arrlatunit[$itr]}
        unit=${arrlatunit[$itr]}
        if [ $unit = 'usec' ]; then
                newlat=`echo $newlat + $lat | bc`
        else
                newlat=`echo "$newlat + $lat * 1000" | bc`
        fi
        itr=$((itr+1))
done

avglat=`echo "scale=3; $newlat / $cnts" | bc`

echo r_iops: $readiops, w_iops: $writeiops, t_iops: $totaliops, avglat: $avglat

# report to mra-mon
hostname=`hostname`
mramon="135.197.227.51"

## graphite
#echo "servers.$hostname.fio.read_iops $readiops `date +%s`" | nc -q0 $mramon 2003
#echo "servers.$hostname.fio.write_iops $writeiops `date +%s`" | nc -q0 $mramon 2003
#echo "servers.$hostname.fio.iops $totaliops `date +%s`" | nc -q0 $mramon 2003
#echo "servers.$hostname.fio.avglat $avglat `date +%s`" | nc -q0 $mramon 2003
#echo "servers.$hostname.fio.bs $bs `date +%s`" | nc -q0 $mramon 2003

# influx
dbname="dss7k"
#echo "curl -i -XPOST \"http://$mramon:8086/write?db=$dbname\" --data-binary \"fio,host=$hostname readiops=$readiops,writeiops=$writeiops,totaliops=$totaliops,avglat=$avglat,bs=$bs\"" >> mra-influxdb.log
curl -i -XPOST "http://$mramon:8086/write?db=$dbname" --data-binary "fio,host=$hostname readiops=$readiops,writeiops=$writeiops,totaliops=$totaliops,avglat=$avglat,bs=$bs"

