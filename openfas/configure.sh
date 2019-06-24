#!/bin/bash
#
#    Copyright (c) 2017 AT&T Labs Research
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#    
#    Author:    Moo-Ryong Ra (mra@research.att.com)
#    History:   2017-01-13 created.
#
#    Abstract:  This script will run on any Linux-based commodity hardware.
#               It will collect necessary configuration details from users
#               and pass it to yaml parser.
#

# Dependency check
sudo apt-get install -y python-pip
sudo apt-get install -y python-yaml

# --------- Questionaire ----------
yamlparser="./parser.py"
outfile="userinput.conf"

declare -a qs
declare -a ans
declare -a var
declare -a inputs

i=0
qs[$i]='* What level of redundancy do you need?' 
ans[$i]='[JBOD|RAID5|RAID6|SKIP]'
var[$i]='redundancy'
inputs[$i]='RAID6'		# default 

i=$((i=i+1))
qs[$i]='* Choose an iSCSI target software.'
ans[$i]='[STGT|LIO|SCST|IET|SKIP]'
var[$i]='target'
inputs[$i]='STGT'		# default

i=$((i=i+1))
qs[$i]='* Do you want to integrate this machine with Openstack (Cinder service)?'
ans[$i]='[Y|N]'
var[$i]='cinder'
inputs[$i]='Y'			# default

i=$((i=i+1))
qs[$i]='* Do you need a data collector for your monitoring stack?'
ans[$i]='[NOPE|TELEGRAF|DIAMOND]'
var[$i]='monitor'
inputs[$i]='TELEGRAF'			# default

args='{'

for idx in `seq 0 $i`;
do
	echo "${qs[$idx]} ${ans[$idx]}, Default: [${inputs[$idx]}]"
	while read -r -p "> " line
	do
		line=`echo $line | awk '{print toupper($0)}'`
		if [[ $line == '' ]]; then
			line=${inputs[$idx]}
		fi

		if [[ ${ans[$idx]} == *"$line"* ]]; then
			break
		fi
		echo "! wrong input. please choose among the valid inputs: ${ans[$idx]}"
	done
	#echo "Okay, ${var[$idx]}=$line"
	if [ "$args" = "{" ] 
	then
		args="$args \"${var[$idx]}\": \"$line\""
	else 
		args="$args, \"${var[$idx]}\": \"$line\""
	fi
done
args="$args }"

echo $args > $outfile

$yamlparser 


