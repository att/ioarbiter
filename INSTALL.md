# Installation

This code is tested under OpenStack Kilo version and assume that there exists a valid Cinder installation.
You need to install IOArbiter components onto both a controller node where a cinder scheduler instance is running and 
a storage node where you want to deploy IOArbiter-capable cinder service.


### OpenStack Controller nodes 

  * Copy source files in src/cinder/ directory to $[cinder-root]/
        
            typical $cinder-root = /usr/lib/python2.7/dist-packages/cinder/

  * Install IOArbiter scheduler filter: add an entry below to cinder-2015.1.1.egg-info/entry_points.txt under [cinder.scheduler.filters] section.
          
              IOArbiterFilter = cinder.scheduler.filters.ioarb_filter:IOArbiterFilter
              

### OpenStack Storage nodes

  * Copy source files in src/cinder/ directory to $[cinder-root]/
        
            typical $cinder-root = /usr/lib/python2.7/dist-packages/cinder/

  * Install madam package.
      
              sudo apt-get install mdadm
          
  * Allowin IOArbiter source to use previleges operations: add two lines below to /etc/cinder/rootwrap.d/volume.filters
          
              mdadm: CommandFilter, mdadm, root
              docker: CommandFilter, docker, root
              
  * Create two directories. set directory permissions as cinder:cinder.
      
              /var/lib/cinder/ioarb-container/
              /var/lib/cinder/ioarb-resv/
              
  * Give a permission for cinder user to run docker commands.
      
              sudo usermod -aG docker cinder

### Volume Type Configuration (Controller node)
        
  * Creat volume types with ioarb_sttype = “ioarbiter”.
     
              cinder type-create $vtype
              cinder type-key $vtype set ioarb_sttype="ioarbiter"
     
  * Set qos-specs fields.
  
              cinder qos-create $vtype ioarb_sttype="ioarb_manual" raidconf=raid6 ndisk=4 miniops=100 maxiops-100 iosize=4096 medium=hdd

      * Available options<br>
      
      > sttype = “[ioarb-demo-platinum|ioarb-demo-gold|ioarb-demo-silver|ioarb-demo-bronze|manual]”<br>
      > raidconf = [jbod|raid0|raid1|raid5|raid6]<br>
      > maxiops<br>
      > miniops<br>
      > iosize = 4096, etc.<br>
      > medium = “ssd|hdd|any”<br>
      > ndisk<br>


### Test
     
  * Create a volume with the volume type you created for IOArbiter service. 
  If you have a Dashboard installed on your cluster, it might be easier to just use the web interface.
