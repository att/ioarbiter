# FIO test scripts

### Prerequisites
```
sudo apt-get install fio bc
```

### Run
- [run.sh](run.sh): a main script to run a test. blocksizes, r/w ratio, iodepth 
   ```
   nohup ./run.sh &
   ```
  
- [exec\_fio.sh](exec_fio.sh): used by run.sh. generate fio configuration, run, and trigger the report script (below).

- [parse\_and\_report\_influxdb.sh](parse_and_report_influxdb.sh): used by `exec_fio.sh`. parse fio output logs and report to influxdb (needs to be preconfigured).

###### Note: current scripts are to test local block devices only (remote version will be added soon).

