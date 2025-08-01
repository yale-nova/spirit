## Spirit cgroups setup
- Update/create `/etc/systemd/system/spirit-snet.slice`
```
[Slice]
CPUAccounting=true
MemoryAccounting=true
BlockIOAccounting=true
```
- Run it with systemctl
```bash
sudo systemctl daemon-reload\nsudo systemctl start spirit-snet.slice
```
- Check if the slice is created
```bash
cat /sys/fs/cgroup/system.slice/spirit-snet.slice/io.stat
cat /sys/fs/cgroup/spirit.slice/spirit-snet.slice/memory.swap.max
```

## How to use this dockerized social network service
- Check configuration variables in Makefile, especially
```make
SOCIAL_NETWORK_DIR=/home/sslee/DeathStarBench/socialNetwork
```
The source code can be cloned from https://github.com/shsym/DeathStarBench.git

- Build a container
```bash
make build
```

- Run the social network service
```bash
make start_service
```

- Run the social network client
```bash
make run
```

- Stop the social network client
```bash
make stop
```

- Or, stop the entire service
```bash
make shutdown_service
```