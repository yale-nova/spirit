#include <rdma/rdma_cma.h>
#include <infiniband/verbs.h>
#include <arpa/inet.h>
#include <stdatomic.h>

#ifndef RDMA_COMMON_H
#define RDMA_COMMON_H

struct mr_info
{
	uint64_t remote_addr;
	uint32_t rkey;
	uint64_t mem_size; // used for client request allocation
} __attribute__((packed));

#define PAGE_SIZE (1 << 12) // 4 KB
#define MIND_DEFAULT_CONTROL_PORT 50001
#define TIMEOUT_IN_MS 5000
#define MIND_QUEUE_SIZE_MAX 128	// maximum in-flight msgs - actual value determined by device capabilities

extern struct sockaddr_in addr;
extern struct rdma_event_channel *ec;
extern struct ibv_qp_init_attr qp_attr;
extern struct rdma_cm_id *conn;
extern struct ibv_pd *pd;
extern struct ibv_mr *mr;
extern struct ibv_cq *cq;
extern char *buffer;
extern uint64_t buffer_size;
extern atomic_uint *alloc_array;
extern struct rdma_cm_event *event;
extern int actual_queue_size;

enum rdma_cm_event_type check_cm_event(void);

void rdma_init(void);
void rdma_init_finish(void);
void rdma_deinit(void);

#endif
