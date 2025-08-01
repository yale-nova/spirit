# Spirit: Fair Resource Allocation in Remote Memory Systems

Spirit is a system designed to fairly allocate interdependent resources in remote memory systems, such as those using RDMA over Converged Ethernet (RoCE). It specifically targets network bandwidth and local memory resources, leveraging a price-driven, auction-based algorithm (Competitive Equilibrium from Equal Incomes, or CEEI).

</br>

*For artifact evaluation, you can directly go to [the artifact evaluation instructions](https://github.com/yale-nova/spirit/blob/main/README.md#artifact-evaluation-instructions).*

</br>

## Repository Overview

This repository contains the code for Spirit, including the resource enforcer, benchmark applications, and datasets. The code is organized as follows:

### Artifact Evaluation

- `ae`: Contains the artifact evaluation scripts and setup instructions. Please check its scripts for detailed system dependencies and configuration.

### Spirit System

- `global-enforcer`: Contains the global resource enforcer implementation.
- `local-enforcer`: Contains the local resource enforcer implementation and scripts to automatically run benchmark applications.

#### Remote Memory System

- `remote_mem`: Contains the remote memory system implementation, which provides remote memory access via a swap partition on a virtual block device, fetching/evicting data from/to the memory node.

#### Supporting Modules

- `bench-mc-client`: Contains the Memcached client implementation.
- `lib`: Contains utility/library code shared between other Rust crates.
- `trace-loader`: Contains the trace loader code to parse and load request traces for benchmarking. It is essentially an adaptor of the [libCacheSim](https://github.com/cacheMon/libCacheSim) library.
- `sample_configs`: Contains sample configuration files for the Spirit cluster and application deployment scenarios (e.g., which applications will be collocated on which compute node).

### Symbiosis Resource Allocation Algorithm

- `res_allocation`: Contains the implementation of the Symbiosis resource allocation algorithm, along with the performance estimator. It also includes infrastructure code to run the algorithm, communicate with resource enforcers to monitor current resource usage and performance, and update the resource allocation.

- `scripts`: Contains scripts to run experiments with the Symbiosis resource allocation algorithm. It also includes scripts to prepare Docker containers for benchmark applications.

## Tested Environment

Spirit was tested on a machine featuring an Intel® Xeon® Gold 6252N CPU and Mellanox/NVIDIA ConnectX-5 NICs. The VMs run Linux 6.13 and are hosted on a Windows Server 2022 machine, which enables PEBS support inside the VMs.

- Each compute VM provides 48 cores, of which 32 are used for running application workloads (e.g., server instances and microservices).

- Each memory VM is provisioned with 120 GB of RAM to store data pages swapped in from the compute VMs.

## Artifact evaluation instructions

### CloudLab Setup

We provide a CloudLab profile for easy setup, which includes the preinstalled Linux kernel used by Spirit and this repository. You can find the profile at [CloudLab Profile](https://www.cloudlab.us/p/MIND-disagg/Spirit-linux-6.13).

The profile uses [xl170](https://www.utah.cloudlab.us/portal/show-nodetype.php?type=xl170) instances equipped with Intel Xeon E5-2640 v4 processors (10 cores) and 64 GB of memory. Our example experiment with two applications (Stream and Memcached) utilizes all CPU cores (with hyperthreading enabled) and nearly all of the memory, especially on the memory node.

1. Start the experiment using the [provided CloudLab profile](https://www.cloudlab.us/p/MIND-disagg/Spirit-linux-6.13). You can find CloudLab documentation [here](https://docs.cloudlab.us/getting-started.html).

2. Once the experiment is running, SSH into the nodes. The first node (node 0) will be the 🖥️compute node, and the second node (node 1) will be the 🗂️memory node (to provide remote memory accessed via RoCE) and the controller (to repurpose its unused CPU cycles).

3. In the 🖥️compute node, run the first initialization script that will configure Intel PEBS and reboot the machine:
```bash
cd /opt/spirit/spirit-controller
cd ae/compute_node
./1.init.sh
```

4. In the 🗂️memory node, run the initialization script that will configure huge pages and reboot the machine:
```bash
cd /opt/spirit/spirit-controller
cd ae/memory_node
./1.init.sh
```

**Note) Rebooting machines usually takes 10+ minutes, so please be patient 😉**
(If you think it gets stuck, you can go to cloudlab's "experiments" page and manually "reboot" servers using the per-node ⚙️ button)

</br>

5. After the reboot, SSH back into the 🖥️compute node and run the second initialization script.

- For 🖥️compuate node:
```bash
cd /opt/spirit/spirit-controller/ae/compute_node
./2.init_after_reboot.sh
```
Please follow the instructions on the screen. The script will configure system dependencies, Docker containers, and the Spirit binaries (resource enforcer, benchmark applications, and dataset; for the artifact evaluation, we used two applications, Stream and Memcached, as examples due to the system resource limit, such as CPUs and memory).

- Similarly, in the 🗂️memory node:
```bash
cd /opt/spirit/spirit-controller/ae/memory_node
./2.init_after_reboot.sh
```

This script on the 🗂️memory node will run the remote memory server program and configure a Jupyter notebook that will guide you through the artifact evaluation.

6. Follow the instructions in the Jupyter notebook (🗂️). To open the web interface, you may want to use `-L` option to forward the port from the 🗂️memory node to your local machine.

You can open a new ssh session to the memory node with:

```
ssh -L <8888 or local port you want to use>:localhost:8888 <username>@<memory_node_ip>
```

Then, you will be able to access the web interface on your local web browser using
```
http://localhost:<local port above>/notebooks/spirit_ae.ipynb
```

Note) If the Jupyter notebook does not open a file automatically, please open `spirit_ae.ipynb`.

Note) If you need to start only the Jupyter notebook (e.g., if the notebook is terminated), you can use (🗂️):
```bash
cd /opt/spirit/spirit-controller/ae/memory_node
./3.run_notebook.sh
```

## Reference

This repository contains the code for the paper "Spirit: Fair Allocation of Interdependent Resources in Remote Memory Systems," presented at SOSP 2025.
