#ifndef MIND_RAM_DRV_RDMA_H
#define MIND_RAM_DRV_RDMA_H

#include <rdma/ib_verbs.h>
#include <rdma/rdma_cm.h>
#include <rdma/mr_pool.h>

#define MIND_RDMA_CM_TIMEOUT_MS 10000
#define MIND_RDMA_CQ_POLL_US 100
#define MIND_RMDA_CQ_POLL_CNT 10

// FIXME: must be synchronized with server-side definition
struct mr_info
{
	__u64 remote_addr;
	__u32 rkey;
	__u64 mem_size; // used for client request allocation
} __attribute__((packed));

struct mind_rdma_req {
    struct ib_rdma_wr rdma_wr;
    struct request_map_entry *entry;
    __u64 task_va;
    struct ib_mr *mr;
    struct scatterlist sglist;
    enum dma_data_direction dir;
};

struct mind_rdma_req *poll_cq(void);
void unmap_mind_req(struct mind_rdma_req *mind_req);
void finish_mind_req(struct mind_rdma_req *mind_req);
struct mind_rdma_req *mind_rdma_read(
	struct request_map_entry *entry, __u64 task_va, void *buf,
	unsigned long addr, unsigned long len);
struct mind_rdma_req *mind_rdma_write(
	struct request_map_entry *entry, __u64 task_va, void *buf,
	unsigned long addr, unsigned long len);

// Global variable for actual queue size determined by device capabilities
extern int actual_queue_size;

#endif